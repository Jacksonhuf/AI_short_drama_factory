"""章节生产运行状态的唯一写入服务。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.production_runs import (
    ChapterProductionRun,
    ChapterProductionRunStep,
    ProductionActorType,
    ProductionRunStatus,
    ProductionRunTransition,
    ProductionStepStatus,
)
from app.models.llm import ModelCategoryKey
from app.models.studio_projects import Chapter
from app.services.llm.preflight import preflight_default_model
from app.services.production_runs.manifest import (
    MANIFEST_VERSION,
    ProductionPreset,
    expand_manifest,
    normalize_config,
)

ACTIVE_RUN_STATUSES = {
    ProductionRunStatus.draft,
    ProductionRunStatus.running,
    ProductionRunStatus.waiting_human,
    ProductionRunStatus.paused,
    ProductionRunStatus.failed,
}


@dataclass(frozen=True)
class TransitionResult:
    """状态写入结果；replay 表示由持久化幂等记录直接复用。"""

    run: ChapterProductionRun
    transition: ProductionRunTransition
    replay: bool
    result_snapshot: dict[str, Any]


def _value(value: object) -> str:
    """统一读取 SQLAlchemy 字符串列可能返回的 Enum 或原始字符串。"""

    return str(getattr(value, "value", value))


def _request_hash(payload: dict[str, Any]) -> str:
    """对状态变更语义生成稳定摘要，用于识别同 key 不同请求。"""

    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _run_snapshot(run: ChapterProductionRun) -> dict[str, Any]:
    """保存 mutation 首次响应所需的运行标量，保证后续重放结果稳定。"""

    def encode_datetime(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    return {
        "id": run.id,
        "project_id": run.project_id,
        "chapter_id": run.chapter_id,
        "preset_key": run.preset_key,
        "manifest_version": run.manifest_version,
        "manifest_snapshot": run.manifest_snapshot,
        "config_snapshot": run.config_snapshot,
        "input_snapshot": run.input_snapshot,
        "target_snapshot_hash": run.target_snapshot_hash,
        "status": _value(run.status),
        "current_step_id": run.current_step_id,
        "lock_version": run.lock_version,
        "transition_version": run.transition_version,
        "cancel_requested": run.cancel_requested,
        "error_code": run.error_code,
        "error_message": run.error_message,
        "started_at": encode_datetime(run.started_at),
        "finished_at": encode_datetime(run.finished_at),
        "created_at": encode_datetime(run.created_at),
        "updated_at": encode_datetime(run.updated_at),
    }


class ProductionRunTransitionService:
    """串行化 run mutation，并持久化乐观锁和幂等审计。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def draft(
        self,
        *,
        chapter_id: str,
        preset: ProductionPreset,
        config: dict[str, Any],
        idempotency_key: str,
        actor_id: str | None = "Admin",
    ) -> TransitionResult:
        """创建 draft run、展开步骤并占用章节活跃槽。"""

        bind = self._db.get_bind()
        if bind.dialect.name == "sqlite" and not self._db.in_transaction():
            # SQLite 忽略 SELECT FOR UPDATE；提前取得写锁可让并发测试稳定落到唯一约束语义。
            await self._db.execute(text("BEGIN IMMEDIATE"))
        try:
            normalized_config = normalize_config(preset, config)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        request_payload = {
            "event_type": "draft",
            "chapter_id": chapter_id,
            "preset": preset.value,
            "config": normalized_config,
        }
        request_hash = _request_hash(request_payload)
        replay = await self._find_create_replay(
            chapter_id=chapter_id,
            idempotency_key=idempotency_key,
        )
        if replay is not None:
            return self._validate_replay(replay, request_hash)

        chapter = await self._db.get(Chapter, chapter_id)
        if chapter is None:
            raise HTTPException(status_code=404, detail="Chapter not found")
        active = await self._active_run(chapter_id)
        if active is not None:
            raise HTTPException(status_code=409, detail="RUN_ALREADY_ACTIVE")

        manifest = expand_manifest(preset, normalized_config)
        run = ChapterProductionRun(
            id=uuid4().hex,
            project_id=chapter.project_id,
            chapter_id=chapter.id,
            preset_key=preset.value,
            manifest_version=MANIFEST_VERSION,
            manifest_snapshot=manifest,
            config_snapshot=normalized_config,
            input_snapshot={
                "chapter_id": chapter.id,
                "chapter_updated_at": (
                    chapter.updated_at.isoformat() if chapter.updated_at is not None else None
                ),
            },
            status=ProductionRunStatus.draft,
            lock_version=0,
            transition_version=0,
            cancel_requested=False,
        )
        steps = [
            ChapterProductionRunStep(
                id=uuid4().hex,
                run_id=run.id,
                stage_key=step["key"],
                sequence=sequence,
                adapter_version=step["adapter_version"],
                execution_mode=step["mode"],
                status=ProductionStepStatus.pending,
                attempt=0,
                idempotency_key=f"{run.id}:step:{sequence}",
            )
            for sequence, step in enumerate(manifest["steps"], start=1)
        ]
        now = datetime.now(UTC)
        # TimestampMixin 的 server defaults 在 flush 后可用于稳定保存首次响应。
        run.created_at = now
        run.updated_at = now
        snapshot = _run_snapshot(run)
        transition = ProductionRunTransition(
            id=uuid4().hex,
            run_id=run.id,
            transition_version=0,
            event_type="draft",
            from_status=None,
            to_status=ProductionRunStatus.draft.value,
            actor_type=ProductionActorType.admin,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            event_payload=request_payload,
            result_snapshot=snapshot,
        )

        try:
            async with self._db.begin_nested():
                self._db.add_all([run, *steps, transition])
                await self._db.flush()
        except IntegrityError:
            # 数据库唯一约束是并发创建的最终裁决；避免依赖 SQLite 的 FOR UPDATE。
            concurrent_replay = await self._find_create_replay(
                chapter_id=chapter_id,
                idempotency_key=idempotency_key,
            )
            if concurrent_replay is not None:
                return self._validate_replay(concurrent_replay, request_hash)
            raise HTTPException(status_code=409, detail="RUN_ALREADY_ACTIVE")
        return TransitionResult(run, transition, False, snapshot)

    async def start(
        self,
        run_id: str,
        *,
        expected_lock_version: int,
        idempotency_key: str,
        actor_id: str | None = "Admin",
    ) -> TransitionResult:
        """模型预检通过后启动运行，并通过 outbox 自动派发首个线性步骤。"""

        result = await self._transition(
            run_id,
            event_type="start",
            expected_lock_version=expected_lock_version,
            idempotency_key=idempotency_key,
            allowed_from={ProductionRunStatus.draft},
            to_status=ProductionRunStatus.running,
            actor_id=actor_id,
            before_transition=self._preflight_start,
        )
        if not result.replay:
            from app.services.production_runs.orchestration import advance_run

            await advance_run(self._db, run_id)
            result.result_snapshot.update(_run_snapshot(result.run))
            result.transition.result_snapshot = result.result_snapshot
        return result

    async def _preflight_start(self, run: ChapterProductionRun) -> None:
        """按 preset 校验启动所需默认模型、供应商配置和本地能力选项。"""

        preset = ProductionPreset(run.preset_key)
        categories = [ModelCategoryKey.text]
        if preset in {
            ProductionPreset.prepare_frames,
            ProductionPreset.controlled_video,
        }:
            categories.append(ModelCategoryKey.image)
        if preset == ProductionPreset.controlled_video:
            categories.append(ModelCategoryKey.video)
        target_ratio = str((run.config_snapshot or {}).get("video_ratio") or "16:9")
        for category in categories:
            await preflight_default_model(
                self._db,
                category=category,
                target_ratio=target_ratio if category != ModelCategoryKey.text else None,
            )

    async def pause(
        self,
        run_id: str,
        *,
        expected_lock_version: int,
        idempotency_key: str,
        actor_id: str | None = "Admin",
    ) -> TransitionResult:
        """暂停正在运行的工作流，不改变步骤执行事实。"""

        return await self._transition(
            run_id,
            event_type="pause",
            expected_lock_version=expected_lock_version,
            idempotency_key=idempotency_key,
            allowed_from={ProductionRunStatus.running},
            to_status=ProductionRunStatus.paused,
            actor_id=actor_id,
        )

    async def resume(
        self,
        run_id: str,
        *,
        expected_lock_version: int,
        idempotency_key: str,
        actor_id: str | None = "Admin",
    ) -> TransitionResult:
        """恢复 paused 运行并继续可执行步骤；waiting_human 仍只能 confirm。"""

        result = await self._transition(
            run_id,
            event_type="resume",
            expected_lock_version=expected_lock_version,
            idempotency_key=idempotency_key,
            allowed_from={ProductionRunStatus.paused},
            to_status=ProductionRunStatus.running,
            actor_id=actor_id,
        )
        if not result.replay:
            from app.services.production_runs.orchestration import advance_run

            await advance_run(self._db, run_id)
            result.result_snapshot.update(_run_snapshot(result.run))
            result.transition.result_snapshot = result.result_snapshot
        return result

    async def cancel(
        self,
        run_id: str,
        *,
        expected_lock_version: int,
        idempotency_key: str,
        actor_id: str | None = "Admin",
    ) -> TransitionResult:
        """取消任意非终态运行并释放章节活跃槽。"""

        return await self._transition(
            run_id,
            event_type="cancel",
            expected_lock_version=expected_lock_version,
            idempotency_key=idempotency_key,
            allowed_from=ACTIVE_RUN_STATUSES,
            to_status=ProductionRunStatus.cancelled,
            actor_id=actor_id,
        )

    async def _transition(
        self,
        run_id: str,
        *,
        event_type: str,
        expected_lock_version: int,
        idempotency_key: str,
        allowed_from: set[ProductionRunStatus],
        to_status: ProductionRunStatus,
        actor_id: str | None,
        before_transition: Callable[[ChapterProductionRun], Awaitable[None]] | None = None,
    ) -> TransitionResult:
        """按 run→当前 step 锁顺序完成一次短事务状态变更。"""

        request_payload = {
            "event_type": event_type,
            "run_id": run_id,
            "expected_lock_version": expected_lock_version,
        }
        request_hash = _request_hash(request_payload)
        replay = await self._find_replay(run_id, idempotency_key)
        if replay is not None:
            return self._validate_replay(replay, request_hash)

        run = await self._locked_run(run_id)
        # 若并发请求在等待锁期间已经写入，获得锁后必须再次检查幂等记录。
        replay = await self._find_replay(run_id, idempotency_key)
        if replay is not None:
            return self._validate_replay(replay, request_hash)
        if run.lock_version != expected_lock_version:
            raise HTTPException(status_code=409, detail="RUN_VERSION_CONFLICT")
        current_status = ProductionRunStatus(_value(run.status))
        if current_status not in allowed_from:
            raise HTTPException(status_code=409, detail="INVALID_RUN_TRANSITION")

        if before_transition is not None:
            # 回调必须保持本地只读；失败发生在任何状态、step 或 outbox 写入之前。
            await before_transition(run)

        if run.current_step_id is not None:
            await self._db.execute(
                select(ChapterProductionRunStep)
                .where(ChapterProductionRunStep.id == run.current_step_id)
                .with_for_update()
            )

        now = datetime.now(UTC)
        run.status = to_status
        run.lock_version += 1
        run.transition_version += 1
        run.updated_at = now
        if event_type == "start":
            run.started_at = run.started_at or now
            first_step = (
                await self._db.execute(
                    select(ChapterProductionRunStep)
                    .where(ChapterProductionRunStep.run_id == run.id)
                    .order_by(ChapterProductionRunStep.sequence)
                    .limit(1)
                )
            ).scalar_one_or_none()
            run.current_step_id = first_step.id if first_step is not None else None
        if event_type == "cancel":
            run.cancel_requested = True
            run.finished_at = now
            if run.current_step_id is not None:
                current_step = await self._db.get(
                    ChapterProductionRunStep, run.current_step_id
                )
                if current_step is not None and _value(current_step.status) not in {
                    ProductionStepStatus.succeeded.value,
                    ProductionStepStatus.failed.value,
                    ProductionStepStatus.skipped.value,
                    ProductionStepStatus.cancelled.value,
                }:
                    current_step.status = ProductionStepStatus.cancelled
                    current_step.finished_at = now
                    current_step.lock_version += 1

        snapshot = _run_snapshot(run)
        transition = ProductionRunTransition(
            id=uuid4().hex,
            run_id=run.id,
            step_id=run.current_step_id,
            transition_version=run.transition_version,
            event_type=event_type,
            from_status=current_status.value,
            to_status=to_status.value,
            actor_type=ProductionActorType.admin,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            event_payload=request_payload,
            result_snapshot=snapshot,
        )
        self._db.add(transition)
        try:
            await self._db.flush()
        except IntegrityError:
            # 正常调用方使用一请求一 session；此分支覆盖跨 session 同 key 的竞态。
            await self._db.rollback()
            replay = await self._find_replay(run_id, idempotency_key)
            if replay is not None:
                return self._validate_replay(replay, request_hash)
            raise
        return TransitionResult(run, transition, False, snapshot)

    async def _locked_run(self, run_id: str) -> ChapterProductionRun:
        """锁定运行行；不存在时统一返回 404。"""

        run = (
            await self._db.execute(
                select(ChapterProductionRun)
                .where(ChapterProductionRun.id == run_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if run is None:
            raise HTTPException(status_code=404, detail="Production run not found")
        return run

    async def _active_run(self, chapter_id: str) -> ChapterProductionRun | None:
        """读取章节当前活跃运行，用于尽早返回领域错误。"""

        return (
            await self._db.execute(
                select(ChapterProductionRun)
                .where(
                    ChapterProductionRun.chapter_id == chapter_id,
                    ChapterProductionRun.status.in_(
                        [status.value for status in ACTIVE_RUN_STATUSES]
                    ),
                )
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _find_replay(
        self, run_id: str, idempotency_key: str
    ) -> ProductionRunTransition | None:
        """按运行和幂等键读取首次 mutation 结果。"""

        return (
            await self._db.execute(
                select(ProductionRunTransition)
                .options(joinedload(ProductionRunTransition.run))
                .where(
                    ProductionRunTransition.run_id == run_id,
                    ProductionRunTransition.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()

    async def _find_create_replay(
        self, *, chapter_id: str, idempotency_key: str
    ) -> ProductionRunTransition | None:
        """创建时 run ID 尚未知，按章节关联查找持久化幂等记录。"""

        return (
            await self._db.execute(
                select(ProductionRunTransition)
                .options(joinedload(ProductionRunTransition.run))
                .join(
                    ChapterProductionRun,
                    ChapterProductionRun.id == ProductionRunTransition.run_id,
                )
                .where(
                    ChapterProductionRun.chapter_id == chapter_id,
                    ProductionRunTransition.idempotency_key == idempotency_key,
                    ProductionRunTransition.event_type == "draft",
                )
            )
        ).scalar_one_or_none()

    def _validate_replay(
        self, transition: ProductionRunTransition, request_hash: str
    ) -> TransitionResult:
        """校验重放语义，拒绝同 key 不同请求。"""

        if transition.request_hash != request_hash:
            raise HTTPException(status_code=409, detail="IDEMPOTENCY_KEY_CONFLICT")
        run = transition.run
        return TransitionResult(run, transition, True, transition.result_snapshot)


# 对外使用领域设计中的简名；长名称保留以明确该服务只处理 production run。
TransitionService = ProductionRunTransitionService
