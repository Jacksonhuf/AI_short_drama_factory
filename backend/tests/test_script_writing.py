"""WP2 AI 剧本写作后端能力测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.contracts.script_writing import (
    ApplyScriptTaskResultRequest,
    ScriptWriteRequest,
    ScriptWriteResult,
)
from app.core.db import Base
from app.models.llm import Model, ModelCategoryKey, ModelSettings, Provider, ProviderStatus
from app.models.script_task_application import ScriptTaskApplication
from app.models.studio import Chapter, Project
from app.models.task import GenerationTask
from app.models.task_dispatch_outbox import TaskDispatchOutbox
from app.models.task_links import GenerationTaskLink
from app.services import script_processing_worker
from app.services.script_processing_worker import ScriptWriteResultGenerator, ScriptWriteTaskExecutor
from app.services.script_writing import (
    SCRIPT_WRITING_RELATION_TYPE,
    apply_script_task_result,
    create_script_write_task,
)
from app.services.worker import task_executor as task_executor_module


def _request_payload(mode: str) -> dict:
    """构造五种模式各自满足前置条件的最小请求。"""

    common = {"mode": mode, "project_id": "project-1", "premise": "主角寻找真相"}
    if mode == "outline_to_chapter":
        common.update(chapter_id="chapter-1", episode_outline=["第一集：相遇"])
    elif mode == "continue_writing":
        common.update(
            chapter_id="chapter-1",
            previous_context="主角发现线索",
            next_stage_goal="追查线索来源",
        )
    elif mode == "rewrite":
        common.update(chapter_id="chapter-1", source_text="原文", rewrite_goal="增强冲突")
    elif mode == "expand_or_compress":
        common.update(chapter_id="chapter-1", source_text="原文", target_length=1200)
    return common


def _result(mode: str = "rewrite", text: str = "候选正文") -> dict:
    """构造符合严格结果契约的候选。"""

    return {
        "mode": mode,
        "title": "候选标题",
        "summary": "候选摘要",
        "script_text": text,
        "episode_outline": [],
        "character_notes": [],
        "continuity_notes": [],
        "assumptions": [],
        "review_questions": [],
        "change_summary": [],
    }


def _project_and_chapter() -> tuple[Project, Chapter]:
    """构造任务创建与 apply 测试所需的最小项目、章节。"""

    project = Project(
        id="project-1",
        name="项目",
        description="",
        style="drama",
        visual_style="live_action",
        seed=0,
        unify_style=True,
        progress=0,
        stats={},
    )
    chapter = Chapter(
        id="chapter-1",
        project_id="project-1",
        index=1,
        title="第一章",
        summary="",
        raw_text="原始正文",
        condensed_text="原始精简正文",
        storyboard_count=0,
        status="draft",
    )
    return project, chapter


def _text_model_config(
    *,
    provider_status: ProviderStatus = ProviderStatus.active,
    api_key: str = "test-key",
) -> tuple[Provider, Model, ModelSettings]:
    """构造可切换供应商状态和凭据的默认文本模型配置。"""

    provider = Provider(
        id="provider-text",
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        api_key=api_key,
        status=provider_status,
    )
    model = Model(
        id="model-text",
        name="gpt-4o-mini",
        category=ModelCategoryKey.text,
        provider_id=provider.id,
    )
    return provider, model, ModelSettings(id=1, default_text_model_id=model.id)


@pytest.mark.parametrize(
    "mode",
    [
        "from_scratch",
        "outline_to_chapter",
        "continue_writing",
        "rewrite",
        "expand_or_compress",
    ],
)
def test_five_script_write_modes_have_valid_contracts(mode: str) -> None:
    request = ScriptWriteRequest.model_validate(_request_payload(mode))
    result = ScriptWriteResult.model_validate(_result(mode))
    assert request.mode.value == mode
    assert result.mode.value == mode


@pytest.mark.asyncio
async def test_create_script_write_task_stages_link_and_outbox() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    session_local = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_local() as db:
        project, chapter = _project_and_chapter()
        db.add_all([project, chapter, *_text_model_config()])
        await db.commit()
        request = ScriptWriteRequest.model_validate(_request_payload("rewrite"))
        created = await create_script_write_task(db, request=request)
        await db.commit()

        task = await db.get(GenerationTask, created.task_id)
        link = (
            await db.execute(
                select(GenerationTaskLink).where(
                    GenerationTaskLink.task_id == created.task_id
                )
            )
        ).scalar_one()
        outbox = (
            await db.execute(
                select(TaskDispatchOutbox).where(TaskDispatchOutbox.task_id == created.task_id)
            )
        ).scalar_one()
        assert task is not None and task.task_kind == "script_write"
        assert link.relation_type == SCRIPT_WRITING_RELATION_TYPE
        assert link.relation_entity_id == "chapter-1"
        assert str(getattr(outbox.status, "value", outbox.status)) == "pending"

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_config", "expected_message"),
    [
        (None, "No default model configured"),
        (_text_model_config(provider_status=ProviderStatus.disabled), "Provider is disabled"),
        (_text_model_config(api_key=""), "api_key is empty"),
    ],
)
async def test_create_script_write_task_preflight_rejects_unavailable_text_model(
    model_config: tuple[Provider, Model, ModelSettings] | None,
    expected_message: str,
) -> None:
    """默认文本模型、供应商状态或凭据缺失时不创建任务和 outbox。"""

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    session_local = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_local() as db:
        project, chapter = _project_and_chapter()
        db.add_all([project, chapter])
        if model_config is not None:
            db.add_all(model_config)
        await db.commit()

        with pytest.raises(HTTPException) as exc_info:
            await create_script_write_task(
                db,
                request=ScriptWriteRequest.model_validate(_request_payload("rewrite")),
            )
        assert exc_info.value.status_code == 503
        assert expected_message in str(exc_info.value.detail)
        assert await db.scalar(select(func.count(GenerationTask.id))) == 0
        assert await db.scalar(select(func.count(TaskDispatchOutbox.id))) == 0

    await engine.dispose()


def test_script_write_generator_uses_default_text_model(monkeypatch) -> None:
    class _FakeLlm:
        """满足 AgentBase 初始化所需的最小默认模型替身。"""

        def bind(self, **_kwargs):
            return self

    sentinel_llm = _FakeLlm()
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        task_executor_module,
        "build_default_text_llm_sync",
        lambda _db, *, thinking: sentinel_llm if thinking else None,
    )

    def _write(self, request):  # noqa: ANN001
        captured["llm"] = self._model
        return ScriptWriteResult.model_validate(_result(request.mode.value))

    monkeypatch.setattr(script_processing_worker.ScriptWriterAgent, "write", _write)
    generated = ScriptWriteResultGenerator().generate(
        object(),
        {"request": _request_payload("from_scratch")},
    )
    assert captured["llm"] is sentinel_llm
    assert generated.mode.value == "from_scratch"


def test_script_write_executor_marks_invalid_result_failed(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "invalid-script-result.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    session_local = sessionmaker(engine, class_=Session, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with session_local() as db:
        db.add(
            GenerationTask(
                id="task-invalid",
                mode="async_polling",
                task_kind="script_write",
                status="pending",
                progress=0,
                payload={"task_kind": "script_write", "run_args": {"request": {}}},
                result=None,
                error="",
            )
        )
        db.commit()

    executor = ScriptWriteTaskExecutor()
    executor._session_maker = session_local
    monkeypatch.setattr(
        executor._generator,
        "generate",
        lambda _db, _args: (_ for _ in ()).throw(
            ValueError("MODEL_OUTPUT_INVALID: invalid script result")
        ),
    )
    executor.run("task-invalid")

    with session_local() as db:
        task = db.get(GenerationTask, "task-invalid")
        assert task is not None
        assert str(getattr(task.status, "value", task.status)) == "failed"
        assert "MODEL_OUTPUT_INVALID" in task.error
    engine.dispose()


async def _prepare_apply_db(tmp_path):
    """创建带成功 script_write 任务的独立异步数据库。"""

    db_path = tmp_path / "apply-script.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    session_local = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with session_local() as db:
        project, chapter = _project_and_chapter()
        task = GenerationTask(
            id="task-write",
            mode="async_polling",
            task_kind="script_write",
            status="succeeded",
            progress=100,
            payload={
                "task_kind": "script_write",
                "run_args": {"request": _request_payload("rewrite")},
            },
            result=_result(),
            error="",
        )
        db.add_all(
            [
                project,
                chapter,
                task,
                GenerationTaskLink(
                    task_id="task-write",
                    resource_type="task_link",
                    relation_type=SCRIPT_WRITING_RELATION_TYPE,
                    relation_entity_id="chapter-1",
                ),
            ]
        )
        await db.commit()
        await db.refresh(chapter)
        expected = chapter.updated_at
    return engine, session_local, expected


@pytest.mark.asyncio
async def test_apply_script_result_success_and_duplicate_replay(tmp_path) -> None:
    engine, session_local, expected = await _prepare_apply_db(tmp_path)
    body = ApplyScriptTaskResultRequest(
        task_id="task-write",
        target_field="raw_text",
        expected_chapter_updated_at=expected,
        idempotency_key="apply-once",
    )
    async with session_local() as db:
        first = await apply_script_task_result(db, chapter_id="chapter-1", request=body)
        await db.commit()
    async with session_local() as db:
        replay = await apply_script_task_result(db, chapter_id="chapter-1", request=body)
        await db.commit()
        chapter = await db.get(Chapter, "chapter-1")
        applications = (await db.execute(select(ScriptTaskApplication))).scalars().all()

    assert first.idempotent_replay is False
    assert replay.idempotent_replay is True
    assert replay.application_id == first.application_id
    assert chapter is not None and chapter.raw_text == "候选正文"
    assert len(applications) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_apply_script_result_rejects_version_conflict(tmp_path) -> None:
    engine, session_local, _expected = await _prepare_apply_db(tmp_path)
    body = ApplyScriptTaskResultRequest(
        task_id="task-write",
        target_field="raw_text",
        expected_chapter_updated_at=datetime(2000, 1, 1, tzinfo=UTC),
        idempotency_key="stale-apply",
    )
    async with session_local() as db:
        with pytest.raises(HTTPException) as exc_info:
            await apply_script_task_result(db, chapter_id="chapter-1", request=body)
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail == "CHAPTER_MODIFIED"
    await engine.dispose()


@pytest.mark.asyncio
async def test_apply_script_result_rejects_invalid_task_result(tmp_path) -> None:
    engine, session_local, expected = await _prepare_apply_db(tmp_path)
    async with session_local() as db:
        task = await db.get(GenerationTask, "task-write")
        assert task is not None
        task.result = {"script_text": ""}
        await db.commit()
    body = ApplyScriptTaskResultRequest(
        task_id="task-write",
        target_field="condensed_text",
        expected_chapter_updated_at=expected,
        idempotency_key="invalid-result",
    )
    async with session_local() as db:
        with pytest.raises(HTTPException) as exc_info:
            await apply_script_task_result(db, chapter_id="chapter-1", request=body)
        assert exc_info.value.status_code == 422
        assert exc_info.value.detail == "TASK_RESULT_INVALID"
    await engine.dispose()
