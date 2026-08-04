"""章节生产运行持久化、生命周期与 WP4 线性编排测试。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateTable

from app.core.db import Base
from app.dependencies import get_db
from app.main import app
from app.models.llm import Model, ModelCategoryKey, ModelSettings, Provider, ProviderStatus
from app.models.production_runs import (
    ChapterProductionRun,
    ChapterProductionRunStep,
    ChapterProductionRunStepItem,
    ProductionRunTaskBinding,
    ProductionRunStatus,
    ProductionStepStatus,
    ProductionRunTransition,
)
from app.models.script_task_application import ScriptTaskApplication
from app.models.studio import Chapter, Project
from app.models.studio_shots import Shot
from app.models.task import GenerationTask, GenerationTaskStatus
from app.models.task_dispatch_outbox import TaskDispatchOutbox
from app.services.production_runs.manifest import (
    MANIFEST_VERSION,
    ProductionPreset,
    expand_manifest,
    normalize_config,
)
from app.services.production_runs.transitions import ProductionRunTransitionService
from app.services.production_runs.orchestration import (
    advance_run,
    confirm_gate,
    mutate_step_item,
    settle_terminal_task,
)
from app.services.production_runs.adapters import GateResult, stage_adapter_registry
from app.services.production_runs.reconciliation import reconcile_production_runs
from app.services.production_runs.dispatch import dispatch_due_run_outboxes
from app.services.task_dispatch import TaskOutboxDispatcher
from app.services import task_dispatch as task_dispatch_service
from app.services.task_terminal_notification import notify_task_terminal
from app.services.script_processing_tasks import (
    AsyncTaskCreateResult,
    create_script_simplification_task,
)
from app.services.task_dispatch import stage_task_dispatch
from app.core.task_manager.types import TaskStatus


def _project_and_chapter(chapter_id: str = "chapter-1") -> tuple[Project, Chapter]:
    """构造生产运行测试所需的最小项目与章节。"""

    project = Project(
        id=f"project-{chapter_id}",
        name="测试项目",
        description="",
        style="真人都市",
        visual_style="现实",
        seed=0,
        unify_style=True,
        progress=0,
        stats={},
    )
    chapter = Chapter(
        id=chapter_id,
        project_id=project.id,
        index=1,
        title="第一章",
        summary="",
        raw_text="正文",
        condensed_text="",
        storyboard_count=0,
        status="draft",
    )
    return project, chapter


async def _prepare_database(path: Path):
    """创建隔离数据库，并写入章节及三个类别的可用默认模型。"""

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{path}",
        connect_args={"timeout": 30},
    )
    session_local = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with session_local() as db:
        provider = Provider(
            id="provider-default",
            name="OpenAI",
            base_url="https://api.openai.com/v1",
            api_key="test-key",
            status=ProviderStatus.active,
        )
        models = [
            Model(
                id=f"model-{category.value}",
                name=name,
                category=category,
                provider_id=provider.id,
            )
            for category, name in (
                (ModelCategoryKey.text, "gpt-4o-mini"),
                (ModelCategoryKey.image, "gpt-image-1"),
                (ModelCategoryKey.video, "sora-mini"),
            )
        ]
        db.add_all([
            *_project_and_chapter(),
            provider,
            *models,
            ModelSettings(
                id=1,
                default_text_model_id="model-text",
                default_image_model_id="model-image",
                default_video_model_id="model-video",
            ),
        ])
        await db.commit()
    return engine, session_local


def test_manifest_v1_expands_all_runnable_presets() -> None:
    """manifest 暴露线性与受控生成 preset，且步骤快照稳定独立。"""

    expected = {
        "script_assist": ["script_write", "script_review_gate"],
        "prepare_shots": [
            "script_divide",
            "script_extract",
            "human_preparation_gate",
        ],
        "prepare_frames": [
            "script_divide",
            "script_extract",
            "human_preparation_gate",
            "frame_prompt",
            "frame_image",
            "video_readiness",
        ],
        "controlled_video": [
            "script_divide",
            "script_extract",
            "human_preparation_gate",
            "frame_prompt",
            "frame_image",
            "video_readiness",
            "video_submit_gate",
            "video_generation",
        ],
    }
    assert {preset.value for preset in ProductionPreset} == set(expected)
    for preset, stage_keys in expected.items():
        manifest = expand_manifest(preset)
        assert manifest["version"] == MANIFEST_VERSION == "v1"
        assert [step["key"] for step in manifest["steps"]] == stage_keys

    first = expand_manifest(ProductionPreset.script_assist)
    first["steps"][0]["key"] = "changed"
    assert expand_manifest(ProductionPreset.script_assist)["steps"][0]["key"] == "script_write"


@pytest.mark.parametrize(
    ("config", "expected_prefix"),
    [
        ({}, []),
        ({"simplify_script": True}, ["script_simplify"]),
        ({"check_consistency": True}, ["script_consistency"]),
        (
            {"optimize_script": True},
            ["script_consistency", "script_optimize"],
        ),
        (
            {
                "simplify_script": True,
                "check_consistency": True,
                "optimize_script": True,
            },
            ["script_simplify", "script_consistency", "script_optimize"],
        ),
    ],
)
def test_manifest_expands_optional_script_processing_in_fixed_order(
    config: dict[str, bool],
    expected_prefix: list[str],
) -> None:
    """可选文本节点按固定顺序展开，优化会隐式包含一致性检查。"""

    normalized = normalize_config(ProductionPreset.prepare_shots, config)
    manifest = expand_manifest(ProductionPreset.prepare_shots, normalized)
    assert [step["key"] for step in manifest["steps"]] == [
        *expected_prefix,
        "script_divide",
        "script_extract",
        "human_preparation_gate",
    ]
    assert normalized["check_consistency"] is (
        config.get("check_consistency", False) or config.get("optimize_script", False)
    )


@pytest.mark.asyncio
async def test_controlled_manifest_config_defaults_and_validation(tmp_path: Path) -> None:
    """受控 preset 固化默认配置，并拒绝非法帧类型和参考模式。"""

    engine, session_local = await _prepare_database(tmp_path / "manifest-config.db")
    async with session_local() as db:
        service = ProductionRunTransitionService(db)
        result = await service.draft(
            chapter_id="chapter-1",
            preset=ProductionPreset.prepare_frames,
            config={},
            idempotency_key="defaults",
        )
        assert result.run.config_snapshot == {
            "simplify_script": False,
            "check_consistency": False,
            "optimize_script": False,
            "frame_types": ["first"],
            "reference_mode": "first",
            "video_ratio": "16:9",
        }
        await service.cancel(
            result.run.id,
            expected_lock_version=0,
            idempotency_key="cancel-defaults",
        )
        with pytest.raises(HTTPException) as invalid:
            await service.draft(
                chapter_id="chapter-1",
                preset=ProductionPreset.controlled_video,
                config={"frame_types": ["middle"], "reference_mode": "unknown"},
                idempotency_key="invalid-config",
            )
        assert invalid.value.status_code == 422
        await db.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_video_submit_gate_blocks_empty_frozen_targets(tmp_path: Path) -> None:
    """视频提交 gate 不允许空冻结目标被误判为可提交。"""

    engine, session_local = await _prepare_database(tmp_path / "empty-video-gate.db")
    async with session_local() as db:
        drafted = await ProductionRunTransitionService(db).draft(
            chapter_id="chapter-1",
            preset=ProductionPreset.controlled_video,
            config={},
            idempotency_key="empty-gate-create",
        )
        drafted.run.input_snapshot = {
            **drafted.run.input_snapshot,
            "target_shot_ids": [],
        }
        gate_step = (
            await db.execute(
                select(ChapterProductionRunStep).where(
                    ChapterProductionRunStep.run_id == drafted.run.id,
                    ChapterProductionRunStep.stage_key == "video_submit_gate",
                )
            )
        ).scalar_one()
        result = await stage_adapter_registry.resolve(
            "video_submit_gate"
        ).evaluate_gate(
            db,
            run=drafted.run,
            step=gate_step,
        )
        assert result.passed is False
        assert result.blocked_reasons[0]["code"] == "NO_TARGET_SHOTS"
        assert result.target_snapshot["readiness"] == []
        await db.commit()
    await engine.dispose()


def test_mysql_ddl_uses_generated_active_chapter_constraint() -> None:
    """ORM 与迁移均为 MySQL 生成 active_chapter_id 唯一槽。"""

    ddl = str(
        CreateTable(ChapterProductionRun.__table__).compile(dialect=mysql.dialect())
    )
    assert "GENERATED ALWAYS AS" in ddl
    assert "uq_chapter_production_runs_active_chapter" in ddl

    migration = (
        Path(__file__).parents[1] / "sql" / "011-add-chapter-production-runs.sql"
    ).read_text(encoding="utf-8")
    assert migration.count("CREATE TABLE IF NOT EXISTS") == 5
    assert "GENERATED ALWAYS AS" in migration
    assert "CREATE TABLE chapter_production_runs" not in migration


def test_sqlite_schema_enforces_one_active_run_per_chapter() -> None:
    """SQLite create_all 支持同一生成列语义，终态 NULL 不互相冲突。"""

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    project, chapter = _project_and_chapter()
    with engine.begin() as connection:
        connection.execute(Project.__table__.insert(), {
            "id": project.id,
            "name": project.name,
            "description": "",
            "style": "真人都市",
            "visual_style": "现实",
            "seed": 0,
            "unify_style": True,
            "progress": 0,
            "stats": {},
        })
        connection.execute(Chapter.__table__.insert(), {
            "id": chapter.id,
            "project_id": project.id,
            "index": 1,
            "title": "第一章",
            "summary": "",
            "raw_text": "",
            "condensed_text": "",
            "storyboard_count": 0,
            "status": "draft",
        })
        base = {
            "project_id": project.id,
            "chapter_id": chapter.id,
            "preset_key": "script_assist",
            "manifest_version": "v1",
            "manifest_snapshot": {},
            "config_snapshot": {},
            "input_snapshot": {},
            "lock_version": 0,
            "transition_version": 0,
            "cancel_requested": False,
        }
        connection.execute(
            ChapterProductionRun.__table__.insert(),
            {**base, "id": "terminal-1", "status": "cancelled"},
        )
        connection.execute(
            ChapterProductionRun.__table__.insert(),
            {**base, "id": "terminal-2", "status": "succeeded"},
        )
        connection.execute(
            ChapterProductionRun.__table__.insert(),
            {**base, "id": "active-1", "status": "draft"},
        )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                ChapterProductionRun.__table__.insert(),
                {**base, "id": "active-2", "status": "paused"},
            )
    engine.dispose()


@pytest.mark.asyncio
async def test_transition_service_lifecycle_locking_and_persisted_idempotency(
    tmp_path: Path,
) -> None:
    """合法生命周期写审计；冲突、非法迁移和重放均不增加版本。"""

    engine, session_local = await _prepare_database(tmp_path / "service.db")
    async with session_local() as db:
        service = ProductionRunTransitionService(db)
        drafted = await service.draft(
            chapter_id="chapter-1",
            preset=ProductionPreset.prepare_shots,
            config={},
            idempotency_key="create-1",
        )
        await db.commit()
        assert drafted.run.status == ProductionRunStatus.draft
        assert len(
            (
                await db.execute(
                    select(ChapterProductionRunStep).where(
                        ChapterProductionRunStep.run_id == drafted.run.id
                    )
                )
            ).scalars().all()
        ) == 3

        replay = await service.draft(
            chapter_id="chapter-1",
            preset=ProductionPreset.prepare_shots,
            config={},
            idempotency_key="create-1",
        )
        assert replay.replay is True
        assert replay.run.id == drafted.run.id

        started = await service.start(
            drafted.run.id,
            expected_lock_version=0,
            idempotency_key="start-1",
        )
        await db.commit()
        assert started.run.status == ProductionRunStatus.running
        assert started.run.lock_version == 1
        assert started.run.current_step_id is not None

        start_replay = await service.start(
            drafted.run.id,
            expected_lock_version=0,
            idempotency_key="start-1",
        )
        assert start_replay.replay is True
        assert start_replay.result_snapshot["status"] == "running"
        assert started.run.lock_version == 1

        with pytest.raises(HTTPException, match="RUN_VERSION_CONFLICT"):
            await service.pause(
                drafted.run.id,
                expected_lock_version=0,
                idempotency_key="pause-stale",
            )
        with pytest.raises(HTTPException, match="INVALID_RUN_TRANSITION"):
            await service.resume(
                drafted.run.id,
                expected_lock_version=1,
                idempotency_key="resume-running",
            )
        with pytest.raises(HTTPException, match="IDEMPOTENCY_KEY_CONFLICT"):
            await service.pause(
                drafted.run.id,
                expected_lock_version=1,
                idempotency_key="start-1",
            )

        paused = await service.pause(
            drafted.run.id,
            expected_lock_version=1,
            idempotency_key="pause-1",
        )
        resumed = await service.resume(
            drafted.run.id,
            expected_lock_version=2,
            idempotency_key="resume-1",
        )
        cancelled = await service.cancel(
            drafted.run.id,
            expected_lock_version=3,
            idempotency_key="cancel-1",
        )
        await db.commit()
        assert paused.result_snapshot["status"] == "paused"
        assert resumed.result_snapshot["status"] == "running"
        assert cancelled.result_snapshot["status"] == "cancelled"
        assert cancelled.run.lock_version == 4
        transition_count = await db.scalar(
            select(func.count(ProductionRunTransition.id)).where(
                ProductionRunTransition.run_id == drafted.run.id
            )
        )
        assert transition_count == 5
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("preset", "missing_field", "category_label"),
    [
        (ProductionPreset.script_assist, "default_text_model_id", "文本模型不可用"),
        (ProductionPreset.prepare_frames, "default_image_model_id", "图片模型不可用"),
        (ProductionPreset.controlled_video, "default_video_model_id", "视频模型不可用"),
    ],
)
async def test_start_preflight_keeps_draft_when_required_model_is_missing(
    tmp_path: Path,
    preset: ProductionPreset,
    missing_field: str,
    category_label: str,
) -> None:
    """各 preset 所需模型缺失时保持 draft，且不创建任务或 outbox。"""

    engine, session_local = await _prepare_database(
        tmp_path / f"missing-{preset.value}.db"
    )
    async with session_local() as db:
        service = ProductionRunTransitionService(db)
        drafted = await service.draft(
            chapter_id="chapter-1",
            preset=preset,
            config={},
            idempotency_key=f"draft-{preset.value}",
        )
        await db.commit()
        settings = await db.get(ModelSettings, 1)
        assert settings is not None
        setattr(settings, missing_field, None)
        await db.commit()

        with pytest.raises(HTTPException) as exc_info:
            await service.start(
                drafted.run.id,
                expected_lock_version=0,
                idempotency_key=f"start-{preset.value}",
            )
        assert exc_info.value.status_code == 503
        assert category_label in str(exc_info.value.detail)
        await db.refresh(drafted.run)
        assert drafted.run.status == ProductionRunStatus.draft
        assert drafted.run.lock_version == 0
        assert drafted.run.current_step_id is None
        assert await db.scalar(select(func.count(GenerationTask.id))) == 0
        assert await db.scalar(select(func.count(TaskDispatchOutbox.id))) == 0
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("preset", list(ProductionPreset))
async def test_start_preflight_accepts_complete_model_configuration(
    tmp_path: Path,
    preset: ProductionPreset,
) -> None:
    """四个 preset 在所需默认模型均可用时正常启动并暂存首步 outbox。"""

    engine, session_local = await _prepare_database(
        tmp_path / f"ready-{preset.value}.db"
    )
    async with session_local() as db:
        service = ProductionRunTransitionService(db)
        drafted = await service.draft(
            chapter_id="chapter-1",
            preset=preset,
            config={},
            idempotency_key=f"draft-{preset.value}",
        )
        started = await service.start(
            drafted.run.id,
            expected_lock_version=0,
            idempotency_key=f"start-{preset.value}",
        )
        assert started.run.status == ProductionRunStatus.running
        assert await db.scalar(select(func.count(GenerationTask.id))) == 1
        assert await db.scalar(select(func.count(TaskDispatchOutbox.id))) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_twenty_concurrent_sqlite_creates_leave_one_active_run(
    tmp_path: Path,
) -> None:
    """SQLite 下 20 个独立 session 并发创建，只有一个占用活跃槽。"""

    engine, session_local = await _prepare_database(tmp_path / "concurrent.db")

    async def create_one(index: int) -> str:
        async with session_local() as db:
            try:
                result = await ProductionRunTransitionService(db).draft(
                    chapter_id="chapter-1",
                    preset=ProductionPreset.script_assist,
                    config={},
                    idempotency_key=f"create-{index}",
                )
                await db.commit()
                return result.run.id
            except HTTPException as error:
                await db.rollback()
                assert error.status_code == 409
                assert error.detail == "RUN_ALREADY_ACTIVE"
                return ""

    results = await asyncio.gather(*(create_one(index) for index in range(20)))
    assert len([run_id for run_id in results if run_id]) == 1
    async with session_local() as db:
        active_count = await db.scalar(
            select(func.count(ChapterProductionRun.id)).where(
                ChapterProductionRun.status.in_(
                    ["draft", "running", "waiting_human", "paused", "failed"]
                )
            )
        )
        assert active_count == 1
    await engine.dispose()


def test_production_run_api_create_list_detail_and_lifecycle(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Studio API 全链路使用 typed ApiResponse，并由 service 完成全部状态写入。"""

    database_path = tmp_path / "api.db"

    async def prepare():
        return await _prepare_database(database_path)

    engine, session_local = asyncio.run(prepare())
    dispatch_calls: list[str] = []

    async def fake_dispatch_after_commit(_db, *, run_id: str):
        """隔离 Celery，并记录 API 的提交后快速投递调用。"""

        dispatch_calls.append(run_id)
        return []

    monkeypatch.setattr(
        "app.api.v1.routes.studio.production_runs.dispatch_due_run_outboxes",
        fake_dispatch_after_commit,
    )

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_local() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    app.dependency_overrides[get_db] = override_db
    try:
        created = client.post(
            "/api/v1/studio/chapters/chapter-1/production-runs",
            json={
                "preset": "script_assist",
                "config": {},
                "idempotency_key": "api-create",
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert body["code"] == 201
        assert body["data"]["status"] == "draft"
        assert len(body["data"]["steps"]) == 2
        run_id = body["data"]["id"]

        duplicate = client.post(
            "/api/v1/studio/chapters/chapter-1/production-runs",
            json={
                "preset": "script_assist",
                "config": {},
                "idempotency_key": "api-create",
            },
        )
        assert duplicate.status_code == 201
        assert duplicate.json()["data"]["id"] == run_id

        listed = client.get("/api/v1/studio/chapters/chapter-1/production-runs")
        assert listed.status_code == 200
        assert listed.json()["data"]["pagination"]["total"] == 1
        detailed = client.get(f"/api/v1/studio/production-runs/{run_id}")
        assert detailed.status_code == 200
        assert detailed.json()["data"]["manifest_version"] == "v1"

        mutations = [
            ("start", 0, "running"),
            ("pause", 1, "paused"),
            ("resume", 2, "running"),
            ("cancel", 3, "cancelled"),
        ]
        for action, lock_version, expected_status in mutations:
            response = client.post(
                f"/api/v1/studio/production-runs/{run_id}/{action}",
                json={
                    "expected_lock_version": lock_version,
                    "idempotency_key": f"api-{action}",
                },
            )
            assert response.status_code == 200
            assert response.json()["data"]["status"] == expected_status

        cancel_replay = client.post(
            f"/api/v1/studio/production-runs/{run_id}/cancel",
            json={"expected_lock_version": 3, "idempotency_key": "api-cancel"},
        )
        assert cancel_replay.status_code == 200
        assert cancel_replay.json()["data"]["lock_version"] == 4
        assert dispatch_calls == [run_id, run_id]

        replacement = client.post(
            "/api/v1/studio/chapters/chapter-1/production-runs",
            json={
                "preset": "prepare_shots",
                "config": {},
                "idempotency_key": "api-replacement",
            },
        )
        assert replacement.status_code == 201
    finally:
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


async def _bindings(
    db: AsyncSession,
    run_id: str,
) -> list[ProductionRunTaskBinding]:
    """读取一次 run 的有序 task binding，供线性编排断言复用。"""

    return (
        await db.execute(
            select(ProductionRunTaskBinding)
            .join(
                ChapterProductionRunStep,
                ChapterProductionRunStep.id == ProductionRunTaskBinding.step_id,
            )
            .where(ProductionRunTaskBinding.run_id == run_id)
            .order_by(
                ChapterProductionRunStep.step_order,
                ProductionRunTaskBinding.attempt,
            )
        )
    ).scalars().all()


async def _succeed_task(
    db: AsyncSession,
    task_id: str,
    *,
    result: dict | None = None,
) -> GenerationTask:
    """模拟 worker 已提交任务成功，但尚未发送 terminal notification。"""

    task = await db.get(GenerationTask, task_id)
    assert task is not None
    task.status = GenerationTaskStatus.succeeded
    task.progress = 100
    task.result = result or {"ok": True}
    task.finished_at = datetime.now(UTC)
    await db.flush()
    return task


@pytest.mark.asyncio
async def test_start_due_outbox_dispatches_exactly_once_after_commit(
    tmp_path: Path,
) -> None:
    """start 提交后 helper 投递首步一次，重复调用由 outbox 状态幂等跳过。"""

    database_path = tmp_path / "start-dispatch.db"
    engine, session_local = await _prepare_database(database_path)
    async with session_local() as db:
        service = ProductionRunTransitionService(db)
        drafted = await service.draft(
            chapter_id="chapter-1",
            preset=ProductionPreset.script_assist,
            config={},
            idempotency_key="dispatch-create",
        )
        started = await service.start(
            drafted.run.id,
            expected_lock_version=0,
            idempotency_key="dispatch-start",
        )
        task_id = (await _bindings(db, started.run.id))[0].task_id
        await db.commit()

        sync_engine = create_engine(f"sqlite:///{database_path}", future=True)
        sync_maker = sessionmaker(
            sync_engine,
            class_=Session,
            expire_on_commit=False,
        )
        published: list[str] = []

        def publish(published_task_id: str, executor_task_id: str) -> SimpleNamespace:
            """记录 publish，模拟 Celery 返回稳定 executor id。"""

            published.append(published_task_id)
            return SimpleNamespace(id=executor_task_id)

        dispatcher = TaskOutboxDispatcher(
            session_maker=sync_maker,
            publisher=publish,
        )
        assert await dispatch_due_run_outboxes(
            db,
            run_id=started.run.id,
            dispatcher=dispatcher.dispatch_task,
        ) == [task_id]
        assert await dispatch_due_run_outboxes(
            db,
            run_id=started.run.id,
            dispatcher=dispatcher.dispatch_task,
        ) == []
        assert published == [task_id]
        sync_engine.dispose()
    await engine.dispose()


@pytest.mark.asyncio
async def test_script_assist_dispatches_binds_confirms_and_deduplicates_terminal(
    tmp_path: Path,
) -> None:
    """script_assist 自动派发，显式应用后才可确认，重复通知不重复推进。"""

    engine, session_local = await _prepare_database(tmp_path / "script-assist.db")
    async with session_local() as db:
        service = ProductionRunTransitionService(db)
        drafted = await service.draft(
            chapter_id="chapter-1",
            preset=ProductionPreset.script_assist,
            config={},
            idempotency_key="script-create",
        )
        started = await service.start(
            drafted.run.id,
            expected_lock_version=0,
            idempotency_key="script-start",
        )
        bindings = await _bindings(db, drafted.run.id)
        assert [binding.task_kind for binding in bindings] == ["script_write"]
        assert await db.scalar(
            select(func.count(TaskDispatchOutbox.id)).where(
                TaskDispatchOutbox.task_id == bindings[0].task_id
            )
        ) == 1

        await _succeed_task(db, bindings[0].task_id)
        assert await settle_terminal_task(db, bindings[0].task_id) is True
        assert await settle_terminal_task(db, bindings[0].task_id) is False
        await db.refresh(started.run)
        assert started.run.status == ProductionRunStatus.waiting_human
        gate_step_id = started.run.current_step_id
        assert gate_step_id is not None

        with pytest.raises(HTTPException) as gate_error:
            await confirm_gate(
                db,
                run_id=started.run.id,
                step_id=gate_step_id,
                expected_lock_version=started.run.lock_version,
                idempotency_key="script-confirm-fail",
            )
        assert gate_error.value.detail["code"] == "GATE_NOT_READY"
        with pytest.raises(HTTPException, match="INVALID_RUN_TRANSITION"):
            await service.resume(
                started.run.id,
                expected_lock_version=started.run.lock_version,
                idempotency_key="script-resume-gate",
            )

        now = datetime.now(UTC).replace(tzinfo=None)
        db.add(
            ScriptTaskApplication(
                id=uuid4().hex,
                task_id=bindings[0].task_id,
                chapter_id="chapter-1",
                target_field="raw_text",
                idempotency_key="script-application",
                expected_chapter_updated_at=now,
                chapter_updated_at=now,
            )
        )
        await db.flush()
        run, _, replay = await confirm_gate(
            db,
            run_id=started.run.id,
            step_id=gate_step_id,
            expected_lock_version=started.run.lock_version,
            idempotency_key="script-confirm",
        )
        assert replay is False
        assert run.status == ProductionRunStatus.succeeded
        await db.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_script_processing_results_flow_through_automatic_run(
    tmp_path: Path,
) -> None:
    """精简、一致性、优化结果经 binding 自动传给 divide 与 extract。"""

    engine, session_local = await _prepare_database(tmp_path / "script-processing-flow.db")
    async with session_local() as db:
        service = ProductionRunTransitionService(db)
        drafted = await service.draft(
            chapter_id="chapter-1",
            preset=ProductionPreset.prepare_shots,
            config={
                "simplify_script": True,
                "optimize_script": True,
            },
            idempotency_key="script-flow-create",
        )
        assert [
            step["key"] for step in drafted.run.manifest_snapshot["steps"]
        ][:3] == ["script_simplify", "script_consistency", "script_optimize"]
        assert drafted.run.config_snapshot["check_consistency"] is True

        started = await service.start(
            drafted.run.id,
            expected_lock_version=0,
            idempotency_key="script-flow-start",
        )
        bindings = await _bindings(db, started.run.id)
        assert [binding.task_kind for binding in bindings] == ["script_simplify"]
        simplify_task = await db.get(GenerationTask, bindings[-1].task_id)
        assert simplify_task is not None
        assert simplify_task.payload["run_args"]["script_text"] == "正文"

        await _succeed_task(
            db,
            simplify_task.id,
            result={
                "simplified_script_text": "精简正文",
                "simplification_summary": "移除重复",
            },
        )
        await settle_terminal_task(db, simplify_task.id)
        bindings = await _bindings(db, started.run.id)
        assert [binding.task_kind for binding in bindings] == [
            "script_simplify",
            "script_consistency",
        ]
        consistency_task = await db.get(GenerationTask, bindings[-1].task_id)
        assert consistency_task is not None
        assert consistency_task.payload["run_args"]["script_text"] == "精简正文"

        consistency_result = {
            "has_issues": True,
            "issues": [{"description": "角色称谓不一致"}],
            "summary": "需要修正",
        }
        await _succeed_task(db, consistency_task.id, result=consistency_result)
        await settle_terminal_task(db, consistency_task.id)
        optimize_binding = (await _bindings(db, started.run.id))[-1]
        assert optimize_binding.task_kind == "script_optimize"
        optimize_task = await db.get(GenerationTask, optimize_binding.task_id)
        assert optimize_task is not None
        assert optimize_task.payload["run_args"] == {
            "script_text": "精简正文",
            "consistency": consistency_result,
        }

        await _succeed_task(
            db,
            optimize_task.id,
            result={
                "optimized_script_text": "优化正文",
                "change_summary": "统一角色称谓",
            },
        )
        await settle_terminal_task(db, optimize_task.id)
        divide_binding = (await _bindings(db, started.run.id))[-1]
        assert divide_binding.task_kind == "script_divide"
        divide_task = await db.get(GenerationTask, divide_binding.task_id)
        assert divide_task is not None
        assert divide_task.payload["run_args"]["script_text"] == "优化正文"

        division_result = {"shots": [{"index": 1, "title": "镜头"}]}
        await _succeed_task(db, divide_task.id, result=division_result)
        await settle_terminal_task(db, divide_task.id)
        extract_binding = (await _bindings(db, started.run.id))[-1]
        assert extract_binding.task_kind == "script_extract"
        extract_task = await db.get(GenerationTask, extract_binding.task_id)
        assert extract_task is not None
        assert extract_task.payload["run_args"]["script_division"] == division_result
        assert extract_task.payload["run_args"]["consistency"] == consistency_result
        all_bindings = await _bindings(db, started.run.id)
        bound_outbox_task_ids = set(
            (
                await db.execute(
                    select(TaskDispatchOutbox.task_id).where(
                        TaskDispatchOutbox.task_id.in_(
                            [binding.task_id for binding in all_bindings]
                        )
                    )
                )
            ).scalars()
        )
        assert bound_outbox_task_ids == {
            binding.task_id for binding in all_bindings
        }
        assert {"script_simplify", "script_consistency", "script_optimize"} <= {
            binding.task_kind
            for binding in all_bindings
            if binding.task_id in bound_outbox_task_ids
        }
        await db.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_reused_external_script_task_fails_without_binding_or_outbox(
    tmp_path: Path,
) -> None:
    """外部活动文本任务不得被 production run 复用、重复投递或绑定。"""

    engine, session_local = await _prepare_database(tmp_path / "active-task-conflict.db")
    async with session_local() as db:
        external = await create_script_simplification_task(
            db,
            relation_entity_id="chapter-1",
            script_text="外部任务文本",
        )
        assert external.reused is False
        drafted = await ProductionRunTransitionService(db).draft(
            chapter_id="chapter-1",
            preset=ProductionPreset.prepare_shots,
            config={"simplify_script": True},
            idempotency_key="active-conflict-create",
        )
        started = await ProductionRunTransitionService(db).start(
            drafted.run.id,
            expected_lock_version=0,
            idempotency_key="active-conflict-start",
        )
        assert started.run.status == ProductionRunStatus.failed
        assert started.run.error_code == "STAGE_PREFLIGHT_FAILED"
        assert started.run.error_message == "ACTIVE_TASK_CONFLICT"
        assert await _bindings(db, started.run.id) == []
        assert await db.scalar(
            select(func.count(TaskDispatchOutbox.id)).where(
                TaskDispatchOutbox.task_id == external.task_id
            )
        ) == 0
        await db.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_prepare_shots_reconciliation_recovers_notification_and_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reconciler 从丢失通知恢复 divide→extract，并由 preparation gate 收尾。"""

    engine, session_local = await _prepare_database(tmp_path / "prepare-shots.db")
    async with session_local() as db:
        service = ProductionRunTransitionService(db)
        drafted = await service.draft(
            chapter_id="chapter-1",
            preset=ProductionPreset.prepare_shots,
            config={},
            idempotency_key="prepare-create",
        )
        started = await service.start(
            drafted.run.id,
            expected_lock_version=0,
            idempotency_key="prepare-start",
        )
        divide_binding = (await _bindings(db, started.run.id))[0]
        await _succeed_task(
            db,
            divide_binding.task_id,
            result={"shots": [{"index": 1, "title": "镜头"}]},
        )

        assert await reconcile_production_runs(db) >= 1
        bindings = await _bindings(db, started.run.id)
        assert [binding.task_kind for binding in bindings] == [
            "script_divide",
            "script_extract",
        ]
        await _succeed_task(db, bindings[1].task_id, result={"draft": {}, "from_cache": False})
        assert await reconcile_production_runs(db) >= 1
        await db.refresh(started.run)
        assert started.run.status == ProductionRunStatus.waiting_human
        gate_step_id = started.run.current_step_id
        assert gate_step_id is not None

        with pytest.raises(HTTPException) as gate_error:
            await confirm_gate(
                db,
                run_id=started.run.id,
                step_id=gate_step_id,
                expected_lock_version=started.run.lock_version,
                idempotency_key="prepare-empty-gate",
            )
        assert gate_error.value.detail["blocked_reasons"][0]["code"] == "NO_TARGET_SHOTS"

        db.add(
            Shot(
                id="shot-1",
                chapter_id="chapter-1",
                index=1,
                title="镜头一",
                status="ready",
                script_excerpt="动作",
            )
        )
        await db.flush()

        async def ready_state(*_args, **_kwargs):
            """为 gate 单测隔离 preparation-state 的大量关联 fixture。"""

            return SimpleNamespace(ready_for_generation=True)

        monkeypatch.setattr(
            "app.services.production_runs.adapters.build_shot_preparation_state",
            ready_state,
        )
        run, _, _ = await confirm_gate(
            db,
            run_id=started.run.id,
            step_id=gate_step_id,
            expected_lock_version=started.run.lock_version,
            idempotency_key="prepare-confirm",
        )
        assert run.status == ProductionRunStatus.succeeded
        await db.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_worker_terminal_notification_persists_and_advances_binding(
    tmp_path: Path,
) -> None:
    """同步 worker 通知使用新事务结算 binding，并推进到 script review gate。"""

    database_path = tmp_path / "worker-notification.db"
    engine, session_local = await _prepare_database(database_path)
    async with session_local() as db:
        service = ProductionRunTransitionService(db)
        drafted = await service.draft(
            chapter_id="chapter-1",
            preset=ProductionPreset.script_assist,
            config={},
            idempotency_key="worker-create",
        )
        started = await service.start(
            drafted.run.id,
            expected_lock_version=0,
            idempotency_key="worker-start",
        )
        binding = (await _bindings(db, started.run.id))[0]
        await _succeed_task(db, binding.task_id)
        await db.commit()

    sync_engine = create_engine(f"sqlite:///{database_path}", future=True)
    sync_maker = sessionmaker(
        sync_engine,
        class_=Session,
        expire_on_commit=False,
    )
    assert notify_task_terminal(binding.task_id, session_maker=sync_maker) is True
    async with session_local() as db:
        run = await db.get(ChapterProductionRun, started.run.id)
        persisted_binding = await db.get(ProductionRunTaskBinding, binding.id)
        assert run is not None and run.status == ProductionRunStatus.waiting_human
        assert persisted_binding is not None and persisted_binding.notified_at is not None
    sync_engine.dispose()
    await engine.dispose()


@pytest.mark.asyncio
async def test_terminal_settlement_dispatches_created_next_task_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """divide 终态提交后，settlement 创建并即时投递 extract，且不重复 publish。"""

    database_path = tmp_path / "terminal-next-dispatch.db"
    engine, session_local = await _prepare_database(database_path)
    async with session_local() as db:
        service = ProductionRunTransitionService(db)
        drafted = await service.draft(
            chapter_id="chapter-1",
            preset=ProductionPreset.prepare_shots,
            config={},
            idempotency_key="terminal-dispatch-create",
        )
        started = await service.start(
            drafted.run.id,
            expected_lock_version=0,
            idempotency_key="terminal-dispatch-start",
        )
        divide_binding = (await _bindings(db, started.run.id))[0]
        await _succeed_task(
            db,
            divide_binding.task_id,
            result={"shots": [{"index": 1, "title": "镜头"}]},
        )
        await db.commit()

    sync_engine = create_engine(f"sqlite:///{database_path}", future=True)
    sync_maker = sessionmaker(
        sync_engine,
        class_=Session,
        expire_on_commit=False,
    )
    published: list[str] = []

    def publish(task_id: str, executor_task_id: str) -> SimpleNamespace:
        """记录 terminal settlement 之后的实际 Celery publish。"""

        published.append(task_id)
        return SimpleNamespace(id=executor_task_id)

    dispatcher = TaskOutboxDispatcher(
        session_maker=sync_maker,
        publisher=publish,
    )
    monkeypatch.setattr(
        task_dispatch_service,
        "TaskOutboxDispatcher",
        lambda **_kwargs: dispatcher,
    )
    assert notify_task_terminal(
        divide_binding.task_id,
        session_maker=sync_maker,
    ) is True
    async with session_local() as db:
        bindings = await _bindings(db, started.run.id)
        assert [binding.task_kind for binding in bindings] == [
            "script_divide",
            "script_extract",
        ]
        extract_task_id = bindings[1].task_id
        assert published == [extract_task_id]
        await dispatch_due_run_outboxes(
            db,
            run_id=started.run.id,
            dispatcher=dispatcher.dispatch_task,
        )
        assert published == [extract_task_id]
    sync_engine.dispose()
    await engine.dispose()


@pytest.mark.asyncio
async def test_cancelled_run_never_dispatches_following_step(tmp_path: Path) -> None:
    """取消后即使活动任务迟到成功，终态结算也不会创建下一任务。"""

    engine, session_local = await _prepare_database(tmp_path / "cancelled-run.db")
    async with session_local() as db:
        service = ProductionRunTransitionService(db)
        drafted = await service.draft(
            chapter_id="chapter-1",
            preset=ProductionPreset.prepare_shots,
            config={},
            idempotency_key="cancel-create",
        )
        started = await service.start(
            drafted.run.id,
            expected_lock_version=0,
            idempotency_key="cancel-start",
        )
        first_binding = (await _bindings(db, started.run.id))[0]
        cancelled = await service.cancel(
            started.run.id,
            expected_lock_version=started.run.lock_version,
            idempotency_key="cancel-run",
        )
        await _succeed_task(db, first_binding.task_id)
        assert await settle_terminal_task(db, first_binding.task_id) is True
        assert cancelled.run.status == ProductionRunStatus.cancelled
        assert len(await _bindings(db, started.run.id)) == 1
        assert await reconcile_production_runs(db) == 0
        await db.commit()
    await engine.dispose()


async def _position_run_at_stage(
    db: AsyncSession,
    *,
    run: ChapterProductionRun,
    stage_key: str,
) -> ChapterProductionRunStep:
    """将测试运行定位到后半段阶段，避免重复执行已由 WP4 覆盖的线性前置。"""

    steps = (
        await db.execute(
            select(ChapterProductionRunStep)
            .where(ChapterProductionRunStep.run_id == run.id)
            .order_by(ChapterProductionRunStep.step_order)
        )
    ).scalars().all()
    target = next(step for step in steps if step.stage_key == stage_key)
    for step in steps:
        if step.step_order < target.step_order:
            step.status = ProductionStepStatus.succeeded
    run.current_step_id = target.id
    run.status = ProductionRunStatus.running
    await db.flush()
    return target


def _install_fake_collection_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """让 fan-out 仍创建真实 GenerationTask/outbox，同时隔离模型与存储配置。"""

    async def create(db, *, run, step, item):
        task_id = uuid4().hex
        task = GenerationTask(
            id=task_id,
            mode="async_polling",
            task_kind=step.stage_key,
            status=GenerationTaskStatus.pending,
            progress=0,
            payload={"run_args": {"shot_id": item.entity_id}},
            error="",
            cancel_requested=False,
        )
        db.add(task)
        await db.flush()
        await stage_task_dispatch(db, task_id)
        return AsyncTaskCreateResult(
            task_id=task_id,
            status=TaskStatus.pending,
            reused=False,
            relation_type=step.stage_key,
            relation_entity_id=item.entity_id,
        )

    for stage_key in ("frame_prompt", "frame_image", "video_generation"):
        monkeypatch.setattr(
            stage_adapter_registry.resolve(stage_key),
            "create_item_task",
            create,
        )


@pytest.mark.asyncio
async def test_controlled_video_fanout_partial_retry_readiness_gate_and_result_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """覆盖 partial、定向 retry、readiness 修复、显式视频 gate 与重复通知。"""

    engine, session_local = await _prepare_database(tmp_path / "controlled-video.db")
    _install_fake_collection_tasks(monkeypatch)
    readiness_blocked = {"shot-2": True}
    submit_gate_blocked = {"shot-1": False}

    async def evaluate_readiness(_db, *, run, item):
        del run
        blocked = readiness_blocked.get(item.entity_id, False)
        reasons = (
            [{"code": "DURATION_READY", "message": "请先配置镜头时长"}]
            if blocked
            else []
        )
        snapshot = {"shot_id": item.entity_id, "ready": not blocked}
        return GateResult(not blocked, item.entity_id, snapshot, reasons)

    monkeypatch.setattr(
        stage_adapter_registry.resolve("video_readiness"),
        "evaluate_item",
        evaluate_readiness,
    )

    async def latest_readiness(_db, *, shot_id, reference_mode):
        """模拟 barrier 后实体变化时，submit gate 读取到的最新 readiness。"""

        blocked = submit_gate_blocked.get(shot_id, False)
        checks = [
            SimpleNamespace(
                key="duration_ready",
                ok=not blocked,
                message="请先配置镜头时长" if blocked else "镜头时长已配置",
            )
        ]
        snapshot = {
            "shot_id": shot_id,
            "reference_mode": reference_mode,
            "ready": not blocked,
            "checks": [
                {"key": check.key, "ok": check.ok, "message": check.message}
                for check in checks
            ],
        }
        return SimpleNamespace(
            shot_id=shot_id,
            reference_mode=reference_mode,
            ready=not blocked,
            checks=checks,
            model_dump=lambda **_kwargs: snapshot,
        )

    monkeypatch.setattr(
        "app.services.production_runs.adapters.get_shot_video_readiness",
        latest_readiness,
    )

    async with session_local() as db:
        service = ProductionRunTransitionService(db)
        drafted = await service.draft(
            chapter_id="chapter-1",
            preset=ProductionPreset.controlled_video,
            config={},
            idempotency_key="controlled-create",
        )
        db.add_all([
            Shot(
                id=shot_id,
                chapter_id="chapter-1",
                index=index,
                title=shot_id,
                status="ready",
                script_excerpt="动作",
            )
            for index, shot_id in enumerate(("shot-1", "shot-2"), start=1)
        ])
        frame_prompt = await _position_run_at_stage(
            db,
            run=drafted.run,
            stage_key="frame_prompt",
        )
        assert await advance_run(db, drafted.run.id) is True
        assert drafted.run.input_snapshot["target_shot_ids"] == ["shot-1", "shot-2"]
        assert drafted.run.config_snapshot["frame_types"] == ["first"]
        assert drafted.run.config_snapshot["reference_mode"] == "first"

        prompt_bindings = await _bindings(db, drafted.run.id)
        assert len(prompt_bindings) == 2
        await _succeed_task(db, prompt_bindings[0].task_id)
        failed_task = await db.get(GenerationTask, prompt_bindings[1].task_id)
        assert failed_task is not None
        failed_task.status = GenerationTaskStatus.failed
        failed_task.error = "provider failed"
        await settle_terminal_task(db, prompt_bindings[0].task_id)
        await settle_terminal_task(db, prompt_bindings[1].task_id)
        await db.refresh(drafted.run)
        await db.refresh(frame_prompt)
        assert frame_prompt.status == ProductionStepStatus.partial
        assert drafted.run.status == ProductionRunStatus.waiting_human
        assert await settle_terminal_task(db, prompt_bindings[1].task_id) is False

        failed_item = (
            await db.execute(
                select(ChapterProductionRunStepItem).where(
                    ChapterProductionRunStepItem.step_id == frame_prompt.id,
                    ChapterProductionRunStepItem.status == ProductionStepStatus.failed,
                )
            )
        ).scalar_one()
        retry_lock = drafted.run.lock_version
        await mutate_step_item(
            db,
            run_id=drafted.run.id,
            step_id=frame_prompt.id,
            item_id=failed_item.id,
            action="retry",
            expected_lock_version=retry_lock,
            idempotency_key="retry-frame-prompt",
        )
        retry_binding = (await _bindings(db, drafted.run.id))[-1]
        await _succeed_task(db, retry_binding.task_id)
        await settle_terminal_task(db, retry_binding.task_id)

        frame_image_step = (
            await db.execute(
                select(ChapterProductionRunStep).where(
                    ChapterProductionRunStep.run_id == drafted.run.id,
                    ChapterProductionRunStep.stage_key == "frame_image",
                )
            )
        ).scalar_one()
        image_bindings = [
            binding
            for binding in await _bindings(db, drafted.run.id)
            if binding.step_id == frame_image_step.id
        ]
        assert len(image_bindings) == 2
        for binding in image_bindings:
            await _succeed_task(db, binding.task_id)
            await settle_terminal_task(db, binding.task_id)

        await db.refresh(drafted.run)
        readiness_step = await db.get(ChapterProductionRunStep, drafted.run.current_step_id)
        assert readiness_step is not None
        assert readiness_step.stage_key == "video_readiness"
        assert readiness_step.status == ProductionStepStatus.partial
        blocked_item = (
            await db.execute(
                select(ChapterProductionRunStepItem).where(
                    ChapterProductionRunStepItem.step_id == readiness_step.id,
                    ChapterProductionRunStepItem.status == ProductionStepStatus.failed,
                )
            )
        ).scalar_one()
        readiness_blocked["shot-2"] = False
        await mutate_step_item(
            db,
            run_id=drafted.run.id,
            step_id=readiness_step.id,
            item_id=blocked_item.id,
            action="retry",
            expected_lock_version=drafted.run.lock_version,
            idempotency_key="retry-readiness",
        )
        await db.refresh(drafted.run)
        gate_step = await db.get(ChapterProductionRunStep, drafted.run.current_step_id)
        assert gate_step is not None and gate_step.stage_key == "video_submit_gate"
        assert drafted.run.status == ProductionRunStatus.waiting_human

        # readiness barrier 已通过后修改实体；submit gate 必须读取最新事实并拒绝。
        submit_gate_blocked["shot-1"] = True
        with pytest.raises(HTTPException) as changed_after_barrier:
            await confirm_gate(
                db,
                run_id=drafted.run.id,
                step_id=gate_step.id,
                expected_lock_version=drafted.run.lock_version,
                idempotency_key="confirm-video-submit-blocked",
            )
        detail = changed_after_barrier.value.detail
        assert detail["code"] == "GATE_NOT_READY"
        assert detail["blocked_reasons"] == [{
            "code": "DURATION_READY",
            "message": "请先配置镜头时长",
            "entity_ref": {"type": "shot", "id": "shot-1"},
        }]

        # 修复同一实体后，重新确认会记录最新 readiness 快照并派发视频任务。
        submit_gate_blocked["shot-1"] = False
        await confirm_gate(
            db,
            run_id=drafted.run.id,
            step_id=gate_step.id,
            expected_lock_version=drafted.run.lock_version,
            idempotency_key="confirm-video-submit",
        )
        assert gate_step.target_snapshot["reference_mode"] == "first"
        assert gate_step.target_snapshot["video_ratio"] == "16:9"
        assert [row["shot_id"] for row in gate_step.target_snapshot["readiness"]] == [
            "shot-1",
            "shot-2",
        ]
        assert all(row["ready"] for row in gate_step.target_snapshot["readiness"])
        video_step = (
            await db.execute(
                select(ChapterProductionRunStep).where(
                    ChapterProductionRunStep.run_id == drafted.run.id,
                    ChapterProductionRunStep.stage_key == "video_generation",
                )
            )
        ).scalar_one()
        video_bindings = [
            binding
            for binding in await _bindings(db, drafted.run.id)
            if binding.step_id == video_step.id
        ]
        assert len(video_bindings) == 2
        for index, binding in enumerate(video_bindings, start=1):
            await _succeed_task(
                db,
                binding.task_id,
                result={"file_id": f"video-file-{index}"},
            )
            await settle_terminal_task(db, binding.task_id)
        await db.refresh(drafted.run)
        assert drafted.run.status == ProductionRunStatus.succeeded
        video_items = (
            await db.execute(
                select(ChapterProductionRunStepItem)
                .where(ChapterProductionRunStepItem.step_id == video_step.id)
                .order_by(ChapterProductionRunStepItem.target_key)
            )
        ).scalars().all()
        assert video_items[0].output_ref["file_id"] == "video-file-1"
        await db.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_fanout_skip_and_deleted_frozen_shot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """失败项可定向 skip；冻结后删除镜头会在下一阶段自动记为 skipped。"""

    engine, session_local = await _prepare_database(tmp_path / "skip-delete.db")
    _install_fake_collection_tasks(monkeypatch)
    async with session_local() as db:
        drafted = await ProductionRunTransitionService(db).draft(
            chapter_id="chapter-1",
            preset=ProductionPreset.prepare_frames,
            config={"frame_types": ["first"]},
            idempotency_key="skip-create",
        )
        db.add_all([
            Shot(
                id=shot_id,
                chapter_id="chapter-1",
                index=index,
                title=shot_id,
                status="ready",
                script_excerpt="动作",
            )
            for index, shot_id in enumerate(("shot-1", "shot-2"), start=1)
        ])
        frame_prompt = await _position_run_at_stage(
            db,
            run=drafted.run,
            stage_key="frame_prompt",
        )
        await advance_run(db, drafted.run.id)
        bindings = await _bindings(db, drafted.run.id)
        await _succeed_task(db, bindings[0].task_id)
        failed = await db.get(GenerationTask, bindings[1].task_id)
        assert failed is not None
        failed.status = GenerationTaskStatus.failed
        failed.error = "bad image"
        await settle_terminal_task(db, bindings[0].task_id)
        await settle_terminal_task(db, bindings[1].task_id)
        failed_item = (
            await db.execute(
                select(ChapterProductionRunStepItem).where(
                    ChapterProductionRunStepItem.step_id == frame_prompt.id,
                    ChapterProductionRunStepItem.status == ProductionStepStatus.failed,
                )
            )
        ).scalar_one()
        await db.delete(await db.get(Shot, "shot-2"))
        await mutate_step_item(
            db,
            run_id=drafted.run.id,
            step_id=frame_prompt.id,
            item_id=failed_item.id,
            action="skip",
            expected_lock_version=drafted.run.lock_version,
            idempotency_key="skip-failed",
        )
        frame_image = (
            await db.execute(
                select(ChapterProductionRunStep).where(
                    ChapterProductionRunStep.run_id == drafted.run.id,
                    ChapterProductionRunStep.stage_key == "frame_image",
                )
            )
        ).scalar_one()
        deleted_item = (
            await db.execute(
                select(ChapterProductionRunStepItem).where(
                    ChapterProductionRunStepItem.step_id == frame_image.id,
                    ChapterProductionRunStepItem.entity_id == "shot-2",
                )
            )
        ).scalar_one()
        assert deleted_item.status == ProductionStepStatus.skipped
        assert deleted_item.blocked_reasons[0]["code"] == "TARGET_DELETED"
        await db.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_cancelled_fanout_settles_item_without_advancing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fan-out 取消后的迟到成功只结算 item，不改写 cancelled step 或派发后续。"""

    engine, session_local = await _prepare_database(tmp_path / "cancel-fanout.db")
    _install_fake_collection_tasks(monkeypatch)
    async with session_local() as db:
        service = ProductionRunTransitionService(db)
        drafted = await service.draft(
            chapter_id="chapter-1",
            preset=ProductionPreset.prepare_frames,
            config={},
            idempotency_key="cancel-fanout-create",
        )
        db.add(
            Shot(
                id="shot-1",
                chapter_id="chapter-1",
                index=1,
                title="镜头",
                status="ready",
                script_excerpt="动作",
            )
        )
        step = await _position_run_at_stage(
            db,
            run=drafted.run,
            stage_key="frame_prompt",
        )
        await advance_run(db, drafted.run.id)
        binding = (await _bindings(db, drafted.run.id))[0]
        await service.cancel(
            drafted.run.id,
            expected_lock_version=drafted.run.lock_version,
            idempotency_key="cancel-fanout",
        )
        await _succeed_task(db, binding.task_id)
        assert await settle_terminal_task(db, binding.task_id) is True
        item = await db.get(ChapterProductionRunStepItem, binding.item_id)
        assert drafted.run.status == ProductionRunStatus.cancelled
        assert step.status == ProductionStepStatus.cancelled
        assert item is not None and item.status == ProductionStepStatus.cancelled
        assert len(await _bindings(db, drafted.run.id)) == 1
        await db.commit()
    await engine.dispose()
