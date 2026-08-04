"""WP4 可恢复线性编排、gate 确认与任务终态结算。"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.production_runs import (
    ChapterProductionRun,
    ChapterProductionRunStep,
    ChapterProductionRunStepItem,
    ProductionActorType,
    ProductionBindingRole,
    ProductionRunStatus,
    ProductionRunTaskBinding,
    ProductionRunTransition,
    ProductionStepStatus,
    ProductionExecutionMode,
)
from app.models.studio import Shot
from app.models.task import GenerationTask, GenerationTaskStatus
from app.services.production_runs.adapters import stage_adapter_registry

_RUN_TERMINAL = {
    ProductionRunStatus.succeeded.value,
    ProductionRunStatus.cancelled.value,
}
_TASK_TERMINAL = {
    GenerationTaskStatus.succeeded.value,
    GenerationTaskStatus.failed.value,
    GenerationTaskStatus.cancelled.value,
}


def _value(value: object) -> str:
    """读取 SQLAlchemy 字符串枚举的稳定值。"""

    return str(getattr(value, "value", value))


def _hash(payload: dict[str, Any]) -> str:
    """生成 mutation 请求的稳定哈希。"""

    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _snapshot(run: ChapterProductionRun) -> dict[str, Any]:
    """生成可供 API 幂等重放的完整 run 标量快照。"""

    def timestamp(value: datetime | None) -> str | None:
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
        "started_at": timestamp(run.started_at),
        "finished_at": timestamp(run.finished_at),
        "created_at": timestamp(run.created_at),
        "updated_at": timestamp(run.updated_at),
    }


def _record_transition(
    db: AsyncSession,
    *,
    run: ChapterProductionRun,
    step_id: str | None,
    event_type: str,
    from_status: str,
    to_status: str,
    idempotency_key: str,
    request_payload: dict[str, Any],
    actor_type: ProductionActorType = ProductionActorType.system,
    actor_id: str | None = None,
) -> ProductionRunTransition:
    """在调用方已持有 run 锁时记录一次状态审计。"""

    run.transition_version += 1
    transition = ProductionRunTransition(
        id=uuid4().hex,
        run_id=run.id,
        step_id=step_id,
        transition_version=run.transition_version,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        actor_type=actor_type,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        request_hash=_hash(request_payload),
        event_payload=request_payload,
        result_snapshot={},
    )
    db.add(transition)
    return transition


async def _locked_run(db: AsyncSession, run_id: str) -> ChapterProductionRun:
    """按统一顺序锁定 run。"""

    run = (
        await db.execute(
            select(ChapterProductionRun)
            .where(ChapterProductionRun.id == run_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Production run not found")
    return run


async def _locked_step(
    db: AsyncSession, step_id: str
) -> ChapterProductionRunStep:
    """在 run 后锁定当前 step。"""

    return (
        await db.execute(
            select(ChapterProductionRunStep)
            .where(ChapterProductionRunStep.id == step_id)
            .with_for_update()
        )
    ).scalar_one()


async def _next_step(
    db: AsyncSession,
    *,
    run_id: str,
    after_sequence: int,
) -> ChapterProductionRunStep | None:
    """读取 manifest 中尚未处理的下一线性步骤。"""

    return (
        await db.execute(
            select(ChapterProductionRunStep)
            .where(
                ChapterProductionRunStep.run_id == run_id,
                ChapterProductionRunStep.sequence > after_sequence,
            )
            .order_by(ChapterProductionRunStep.sequence)
            .limit(1)
            .with_for_update()
        )
    ).scalar_one_or_none()


async def _finish_run(
    db: AsyncSession,
    *,
    run: ChapterProductionRun,
    from_status: str,
    event_key: str,
) -> None:
    """将没有后续步骤的运行结算为 succeeded。"""

    now = datetime.now(UTC)
    run.status = ProductionRunStatus.succeeded
    run.current_step_id = None
    run.finished_at = now
    run.updated_at = now
    run.lock_version += 1
    transition = _record_transition(
        db,
        run=run,
        step_id=None,
        event_type="complete",
        from_status=from_status,
        to_status=ProductionRunStatus.succeeded.value,
        idempotency_key=event_key,
        request_payload={"event_type": "complete", "run_id": run.id},
    )
    transition.result_snapshot = _snapshot(run)


_ITEM_TERMINAL = {
    ProductionStepStatus.succeeded.value,
    ProductionStepStatus.failed.value,
    ProductionStepStatus.skipped.value,
    ProductionStepStatus.cancelled.value,
}


async def _ensure_frozen_shots(
    db: AsyncSession,
    *,
    run: ChapterProductionRun,
) -> list[str]:
    """首次进入集合阶段时冻结章节镜头，后续阶段始终复用该快照。"""

    frozen = list(run.input_snapshot.get("target_shot_ids") or [])
    if "target_shot_ids" in run.input_snapshot:
        return frozen
    frozen = list(
        (
            await db.execute(
                select(Shot.id)
                .where(Shot.chapter_id == run.chapter_id)
                .order_by(Shot.index, Shot.id)
            )
        ).scalars()
    )
    snapshot = dict(run.input_snapshot)
    snapshot["target_shot_ids"] = frozen
    run.input_snapshot = snapshot
    run.target_snapshot_hash = _hash({"target_shot_ids": frozen})
    return frozen


def _target_specs(
    *,
    run: ChapterProductionRun,
    step: ChapterProductionRunStep,
    shot_ids: list[str],
) -> list[tuple[str, str]]:
    """根据阶段和冻结镜头生成确定性 ``(shot_id, target_key)``。"""

    if step.stage_key in {"frame_prompt", "frame_image"}:
        return [
            (shot_id, f"{shot_id}:{frame_type}")
            for shot_id in shot_ids
            for frame_type in run.config_snapshot["frame_types"]
        ]
    return [(shot_id, shot_id) for shot_id in shot_ids]


async def _ensure_step_items(
    db: AsyncSession,
    *,
    run: ChapterProductionRun,
    step: ChapterProductionRunStep,
) -> list[ChapterProductionRunStepItem]:
    """幂等创建步骤冻结 item；新增镜头不会进入已有 run。"""

    existing = (
        await db.execute(
            select(ChapterProductionRunStepItem)
            .where(ChapterProductionRunStepItem.step_id == step.id)
            .order_by(ChapterProductionRunStepItem.target_key)
            .with_for_update()
        )
    ).scalars().all()
    if existing:
        return existing
    shot_ids = await _ensure_frozen_shots(db, run=run)
    specs = _target_specs(run=run, step=step, shot_ids=shot_ids)
    items = [
        ChapterProductionRunStepItem(
            id=uuid4().hex,
            step_id=step.id,
            entity_type="shot",
            entity_id=shot_id,
            target_key=target_key,
            status=ProductionStepStatus.pending,
            attempt=0,
            idempotency_key=f"{step.id}:item:{target_key}",
        )
        for shot_id, target_key in specs
    ]
    step.target_snapshot = {
        "target_shot_ids": shot_ids,
        "target_keys": [target_key for _, target_key in specs],
    }
    db.add_all(items)
    await db.flush()
    return items


async def _settle_collection_step(
    db: AsyncSession,
    *,
    run: ChapterProductionRun,
    step: ChapterProductionRunStep,
    items: list[ChapterProductionRunStepItem],
) -> bool:
    """按 item 终态结算 fan-out/barrier，并确保下一阶段只推进一次。"""

    statuses = [_value(item.status) for item in items]
    if any(status not in _ITEM_TERMINAL for status in statuses):
        return False
    if _value(step.status) in {
        ProductionStepStatus.succeeded.value,
        ProductionStepStatus.skipped.value,
        ProductionStepStatus.partial.value,
        ProductionStepStatus.failed.value,
    }:
        return False

    now = datetime.now(UTC)
    counts = {status: statuses.count(status) for status in sorted(set(statuses))}
    step.output_summary = {"total": len(items), "counts": counts}
    step.finished_at = now
    step.lock_version += 1
    failed = (
        counts.get(ProductionStepStatus.failed.value, 0)
        + counts.get(ProductionStepStatus.cancelled.value, 0)
    )
    succeeded = counts.get(ProductionStepStatus.succeeded.value, 0)
    skipped = counts.get(ProductionStepStatus.skipped.value, 0)
    if not items or skipped == len(items):
        step.status = ProductionStepStatus.skipped
    elif failed:
        step.status = (
            ProductionStepStatus.partial if succeeded or skipped else ProductionStepStatus.failed
        )
        previous = _value(run.status)
        run.status = ProductionRunStatus.waiting_human
        run.error_code = "ITEMS_PARTIAL" if succeeded or skipped else "ITEMS_FAILED"
        run.error_message = f"{failed} collection item(s) require retry or skip"
        run.updated_at = now
        run.lock_version += 1
        transition = _record_transition(
            db,
            run=run,
            step_id=step.id,
            event_type="items_wait",
            from_status=previous,
            to_status=ProductionRunStatus.waiting_human.value,
            idempotency_key=f"items-wait:{step.id}:{step.attempt}",
            request_payload={"event_type": "items_wait", "step_id": step.id, "counts": counts},
        )
        transition.result_snapshot = _snapshot(run)
        await db.flush()
        return True
    else:
        step.status = ProductionStepStatus.succeeded
    run.updated_at = now
    await db.flush()
    await advance_run(db, run.id)
    return True


async def _advance_collection_step(
    db: AsyncSession,
    *,
    run: ChapterProductionRun,
    step: ChapterProductionRunStep,
) -> bool:
    """展开并派发 fan-out，或同步评估 readiness barrier。"""

    adapter = stage_adapter_registry.resolve(step.stage_key, step.adapter_version)
    items = await _ensure_step_items(db, run=run, step=step)
    now = datetime.now(UTC)
    if _value(step.status) == ProductionStepStatus.pending.value:
        step.status = ProductionStepStatus.running
        step.attempt += 1
        step.started_at = now
        step.lock_version += 1

    existing_shots = set(
        (
            await db.execute(
                select(Shot.id).where(
                    Shot.id.in_({item.entity_id for item in items})
                )
            )
        ).scalars()
    ) if items else set()
    changed = False
    for item in items:
        if _value(item.status) != ProductionStepStatus.pending.value:
            continue
        if item.entity_id not in existing_shots:
            item.status = ProductionStepStatus.skipped
            item.blocked_reasons = [{
                "code": "TARGET_DELETED",
                "message": "冻结目标镜头已删除",
                "entity_ref": {"type": "shot", "id": item.entity_id},
            }]
            item.finished_at = now
            item.lock_version += 1
            changed = True
            continue
        if _value(step.execution_mode) == ProductionExecutionMode.barrier.value:
            result = await adapter.evaluate_item(db, run=run, item=item)
            item.attempt += 1
            item.status = (
                ProductionStepStatus.succeeded
                if result.passed
                else ProductionStepStatus.failed
            )
            item.output_ref = result.target_snapshot
            item.blocked_reasons = result.blocked_reasons
            item.started_at = item.started_at or now
            item.finished_at = now
            item.lock_version += 1
            changed = True
            continue
        existing_binding = (
            await db.execute(
                select(ProductionRunTaskBinding).where(
                    ProductionRunTaskBinding.item_id == item.id,
                    ProductionRunTaskBinding.attempt == item.attempt + 1,
                )
            )
        ).scalar_one_or_none()
        if existing_binding is not None:
            continue
        try:
            task_info = await adapter.create_item_task(
                db,
                run=run,
                step=step,
                item=item,
            )
        except (ValueError, HTTPException) as exc:
            item.attempt += 1
            item.status = ProductionStepStatus.failed
            item.blocked_reasons = [{
                "code": "ITEM_PREFLIGHT_FAILED",
                "message": str(getattr(exc, "detail", exc)),
            }]
            item.started_at = item.started_at or now
            item.finished_at = now
            item.lock_version += 1
            changed = True
            continue
        item.attempt += 1
        item.status = ProductionStepStatus.running
        item.started_at = item.started_at or now
        item.finished_at = None
        item.blocked_reasons = []
        item.lock_version += 1
        db.add(
            ProductionRunTaskBinding(
                id=uuid4().hex,
                run_id=run.id,
                step_id=step.id,
                item_id=item.id,
                task_id=task_info.task_id,
                attempt=item.attempt,
                task_kind=step.stage_key,
                binding_role=ProductionBindingRole.child,
            )
        )
        changed = True
    await db.flush()
    settled = await _settle_collection_step(db, run=run, step=step, items=items)
    return changed or settled


async def advance_run(  # pylint: disable=too-many-return-statements
    db: AsyncSession, run_id: str
) -> bool:
    """幂等派发当前可执行步骤，或把人工 gate 置为等待。

    该函数只 flush；事务提交和 outbox 的提交后快速投递由调用入口负责。
    """

    run = await _locked_run(db, run_id)
    if _value(run.status) != ProductionRunStatus.running.value or run.cancel_requested:
        return False
    if run.current_step_id is None:
        first = (
            await db.execute(
                select(ChapterProductionRunStep)
                .where(ChapterProductionRunStep.run_id == run.id)
                .order_by(ChapterProductionRunStep.sequence)
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if first is None:
            await _finish_run(
                db,
                run=run,
                from_status=ProductionRunStatus.running.value,
                event_key=f"complete:{run.id}:empty",
            )
            await db.flush()
            return True
        run.current_step_id = first.id

    step = await _locked_step(db, run.current_step_id)
    if _value(step.status) in {
        ProductionStepStatus.succeeded.value,
        ProductionStepStatus.skipped.value,
    }:
        following = await _next_step(
            db,
            run_id=run.id,
            after_sequence=step.sequence,
        )
        if following is None:
            await _finish_run(
                db,
                run=run,
                from_status=ProductionRunStatus.running.value,
                event_key=f"complete:{run.id}:{step.id}",
            )
            await db.flush()
            return True
        run.current_step_id = following.id
        step = following

    if _value(step.execution_mode) in {
        ProductionExecutionMode.fan_out.value,
        ProductionExecutionMode.barrier.value,
    }:
        return await _advance_collection_step(db, run=run, step=step)

    adapter = stage_adapter_registry.resolve(step.stage_key, step.adapter_version)
    now = datetime.now(UTC)
    if adapter.is_gate:
        step.status = ProductionStepStatus.waiting
        step.started_at = step.started_at or now
        step.lock_version += 1
        run.status = ProductionRunStatus.waiting_human
        run.updated_at = now
        run.lock_version += 1
        transition = _record_transition(
            db,
            run=run,
            step_id=step.id,
            event_type="gate_wait",
            from_status=ProductionRunStatus.running.value,
            to_status=ProductionRunStatus.waiting_human.value,
            idempotency_key=f"gate-wait:{step.id}",
            request_payload={"event_type": "gate_wait", "run_id": run.id, "step_id": step.id},
        )
        transition.result_snapshot = _snapshot(run)
        await db.flush()
        return True

    existing_binding = (
        await db.execute(
            select(ProductionRunTaskBinding)
            .where(
                ProductionRunTaskBinding.step_id == step.id,
                ProductionRunTaskBinding.attempt == step.attempt,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing_binding is not None:
        return False

    try:
        task_info = await adapter.create_task(db, run=run, step=step)
    except (ValueError, HTTPException) as exc:
        message = str(getattr(exc, "detail", exc))
        step.status = ProductionStepStatus.failed
        step.blocked_reasons = [{"code": "STAGE_PREFLIGHT_FAILED", "message": message}]
        step.finished_at = now
        step.lock_version += 1
        run.status = ProductionRunStatus.failed
        run.error_code = "STAGE_PREFLIGHT_FAILED"
        run.error_message = message
        run.finished_at = now
        run.updated_at = now
        run.lock_version += 1
        transition = _record_transition(
            db,
            run=run,
            step_id=step.id,
            event_type="stage_failed",
            from_status=ProductionRunStatus.running.value,
            to_status=ProductionRunStatus.failed.value,
            idempotency_key=f"preflight-failed:{step.id}:{step.attempt}",
            request_payload={"event_type": "stage_failed", "step_id": step.id, "message": message},
        )
        transition.result_snapshot = _snapshot(run)
        await db.flush()
        return True

    step.attempt += 1
    step.status = ProductionStepStatus.running
    step.started_at = step.started_at or now
    step.input_snapshot = {
        "relation_type": task_info.relation_type,
        "relation_entity_id": task_info.relation_entity_id,
    }
    step.lock_version += 1
    run.updated_at = now
    db.add(
        ProductionRunTaskBinding(
            id=uuid4().hex,
            run_id=run.id,
            step_id=step.id,
            task_id=task_info.task_id,
            attempt=step.attempt,
            task_kind=step.stage_key,
            binding_role=ProductionBindingRole.primary,
        )
    )
    await db.flush()
    return True


async def settle_terminal_task(  # pylint: disable=too-many-return-statements
    db: AsyncSession,
    task_id: str,
) -> bool:
    """持久化结算 binding/step，并在成功时幂等推进下一步骤。"""

    binding_ref = (
        await db.execute(
            select(ProductionRunTaskBinding)
            .where(ProductionRunTaskBinding.task_id == task_id)
        )
    ).scalar_one_or_none()
    if binding_ref is None:
        return False
    run = await _locked_run(db, binding_ref.run_id)
    step = await _locked_step(db, binding_ref.step_id)
    binding = (
        await db.execute(
            select(ProductionRunTaskBinding)
            .where(ProductionRunTaskBinding.task_id == task_id)
            .with_for_update()
        )
    ).scalar_one()
    if binding.notified_at is not None:
        return False
    task = await db.get(GenerationTask, task_id)
    if task is None or _value(task.status) not in _TASK_TERMINAL:
        return False

    now = datetime.now(UTC)
    task_status = _value(task.status)
    binding.terminal_status = task_status
    binding.notified_at = now

    if binding.item_id is not None:
        item = (
            await db.execute(
                select(ChapterProductionRunStepItem)
                .where(ChapterProductionRunStepItem.id == binding.item_id)
                .with_for_update()
            )
        ).scalar_one()
        # 旧 attempt 的迟到通知只结算历史 binding，不覆盖定向 retry 的当前事实。
        if binding.attempt != item.attempt:
            await db.flush()
            return True
        item.finished_at = now
        item.lock_version += 1
        if _value(run.status) == ProductionRunStatus.cancelled.value:
            item.status = ProductionStepStatus.cancelled
        elif task_status == GenerationTaskStatus.succeeded.value:
            item.status = ProductionStepStatus.succeeded
            result = dict(task.result or {})
            item.output_ref = {
                "task_id": task.id,
                "task_kind": task.task_kind,
                **{
                    key: result[key]
                    for key in ("file_id", "url", "images")
                    if key in result
                },
            }
            item.blocked_reasons = []
        else:
            item.status = (
                ProductionStepStatus.cancelled
                if task_status == GenerationTaskStatus.cancelled.value
                else ProductionStepStatus.failed
            )
            item.blocked_reasons = [{
                "code": "TASK_CANCELLED" if task_status == "cancelled" else "TASK_FAILED",
                "message": task.error or f"{task.task_kind} {task_status}",
            }]
        await db.flush()
        if _value(run.status) == ProductionRunStatus.cancelled.value:
            return True
        items = (
            await db.execute(
                select(ChapterProductionRunStepItem)
                .where(ChapterProductionRunStepItem.step_id == step.id)
                .order_by(ChapterProductionRunStepItem.id)
                .with_for_update()
            )
        ).scalars().all()
        await _settle_collection_step(db, run=run, step=step, items=items)
        return True

    step.finished_at = now
    step.lock_version += 1

    if _value(run.status) == ProductionRunStatus.cancelled.value:
        step.status = ProductionStepStatus.cancelled
        await db.flush()
        return True

    if task_status == GenerationTaskStatus.succeeded.value:
        step.status = ProductionStepStatus.succeeded
        step.output_summary = {"task_id": task.id, "task_kind": task.task_kind}
        previous = _value(run.status)
        run.updated_at = now
        run.lock_version += 1
        transition = _record_transition(
            db,
            run=run,
            step_id=step.id,
            event_type="task_terminal",
            from_status=previous,
            to_status=previous,
            idempotency_key=f"terminal:{task.id}",
            request_payload={
                "event_type": "task_terminal",
                "task_id": task.id,
                "status": task_status,
            },
            actor_type=ProductionActorType.worker,
            actor_id="task.execute",
        )
        transition.result_snapshot = _snapshot(run)
    else:
        step.status = (
            ProductionStepStatus.cancelled
            if task_status == GenerationTaskStatus.cancelled.value
            else ProductionStepStatus.failed
        )
        previous = _value(run.status)
        run.status = ProductionRunStatus.failed
        run.error_code = "TASK_CANCELLED" if task_status == "cancelled" else "TASK_FAILED"
        run.error_message = task.error or f"{task.task_kind} {task_status}"
        run.finished_at = now
        run.updated_at = now
        run.lock_version += 1
        transition = _record_transition(
            db,
            run=run,
            step_id=step.id,
            event_type="task_terminal",
            from_status=previous,
            to_status=ProductionRunStatus.failed.value,
            idempotency_key=f"terminal:{task.id}",
            request_payload={"event_type": "task_terminal", "task_id": task.id, "status": task_status},
            actor_type=ProductionActorType.worker,
            actor_id="task.execute",
        )
        transition.result_snapshot = _snapshot(run)

    await db.flush()
    if task_status == GenerationTaskStatus.succeeded.value:
        # paused 运行会保留已完成事实，但 advance_run 的状态检查禁止派发后续任务。
        await advance_run(db, run.id)
    return True


async def confirm_gate(
    db: AsyncSession,
    *,
    run_id: str,
    step_id: str,
    expected_lock_version: int,
    idempotency_key: str,
    actor_id: str | None = "Admin",
) -> tuple[ChapterProductionRun, ProductionRunTransition, bool]:
    """校验并确认当前 gate；resume 永远不能替代此入口。"""

    payload = {
        "event_type": "confirm",
        "run_id": run_id,
        "step_id": step_id,
        "expected_lock_version": expected_lock_version,
    }
    request_hash = _hash(payload)
    replay = (
        await db.execute(
            select(ProductionRunTransition).where(
                ProductionRunTransition.run_id == run_id,
                ProductionRunTransition.idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if replay is not None:
        if replay.request_hash != request_hash:
            raise HTTPException(status_code=409, detail="IDEMPOTENCY_KEY_CONFLICT")
        run = await db.get(ChapterProductionRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Production run not found")
        return run, replay, True

    run = await _locked_run(db, run_id)
    if run.lock_version != expected_lock_version:
        raise HTTPException(status_code=409, detail="RUN_VERSION_CONFLICT")
    if (
        _value(run.status) != ProductionRunStatus.waiting_human.value
        or run.current_step_id != step_id
    ):
        raise HTTPException(status_code=409, detail="INVALID_GATE_CONFIRM")
    step = await _locked_step(db, step_id)
    adapter = stage_adapter_registry.resolve(step.stage_key, step.adapter_version)
    if not adapter.is_gate:
        raise HTTPException(status_code=409, detail="STEP_IS_NOT_GATE")
    gate = await adapter.evaluate_gate(db, run=run, step=step)
    if not gate.passed:
        raise HTTPException(
            status_code=409,
            detail={"code": "GATE_NOT_READY", "blocked_reasons": gate.blocked_reasons},
        )

    now = datetime.now(UTC)
    previous = _value(run.status)
    step.status = ProductionStepStatus.succeeded
    step.target_snapshot = gate.target_snapshot
    step.gate_snapshot_hash = gate.snapshot_hash
    step.blocked_reasons = []
    step.confirmed_by = actor_id
    step.confirmed_at = now
    step.finished_at = now
    step.lock_version += 1
    run.status = ProductionRunStatus.running
    run.lock_version += 1
    run.updated_at = now
    transition = _record_transition(
        db,
        run=run,
        step_id=step.id,
        event_type="confirm",
        from_status=previous,
        to_status=ProductionRunStatus.running.value,
        idempotency_key=idempotency_key,
        request_payload=payload,
        actor_type=ProductionActorType.admin,
        actor_id=actor_id,
    )
    await db.flush()
    await advance_run(db, run.id)
    await db.refresh(run)
    transition.result_snapshot = _snapshot(run)
    await db.flush()
    return run, transition, False


async def mutate_step_item(
    db: AsyncSession,
    *,
    run_id: str,
    step_id: str,
    item_id: str,
    action: str,
    expected_lock_version: int,
    idempotency_key: str,
    actor_id: str | None = "Admin",
) -> tuple[ChapterProductionRun, ChapterProductionRunStepItem, bool]:
    """定向 retry/skip 一个失败 item，并重新结算当前集合步骤。"""

    if action not in {"retry", "skip"}:
        raise ValueError("unsupported item action")
    payload = {
        "event_type": f"item_{action}",
        "run_id": run_id,
        "step_id": step_id,
        "item_id": item_id,
        "expected_lock_version": expected_lock_version,
    }
    request_hash = _hash(payload)
    replay = (
        await db.execute(
            select(ProductionRunTransition).where(
                ProductionRunTransition.run_id == run_id,
                ProductionRunTransition.idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if replay is not None:
        if replay.request_hash != request_hash:
            raise HTTPException(status_code=409, detail="IDEMPOTENCY_KEY_CONFLICT")
        item = await db.get(ChapterProductionRunStepItem, item_id)
        run = await db.get(ChapterProductionRun, run_id)
        if item is None or run is None:
            raise HTTPException(status_code=404, detail="Production run item not found")
        return run, item, True

    run = await _locked_run(db, run_id)
    if run.lock_version != expected_lock_version:
        raise HTTPException(status_code=409, detail="RUN_VERSION_CONFLICT")
    if run.current_step_id != step_id or _value(run.status) != ProductionRunStatus.waiting_human.value:
        raise HTTPException(status_code=409, detail="INVALID_ITEM_MUTATION")
    step = await _locked_step(db, step_id)
    item = (
        await db.execute(
            select(ChapterProductionRunStepItem)
            .where(
                ChapterProductionRunStepItem.id == item_id,
                ChapterProductionRunStepItem.step_id == step_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Production run item not found")
    if _value(item.status) not in {
        ProductionStepStatus.failed.value,
        ProductionStepStatus.cancelled.value,
    }:
        raise HTTPException(status_code=409, detail="ITEM_NOT_RETRYABLE")

    now = datetime.now(UTC)
    previous = _value(run.status)
    run.status = ProductionRunStatus.running
    run.error_code = None
    run.error_message = None
    run.updated_at = now
    run.lock_version += 1
    step.status = ProductionStepStatus.running
    step.finished_at = None
    step.lock_version += 1
    item.lock_version += 1
    if action == "retry":
        item.status = ProductionStepStatus.pending
        item.blocked_reasons = []
        item.finished_at = None
    else:
        item.status = ProductionStepStatus.skipped
        item.blocked_reasons = [{
            "code": "SKIPPED_BY_USER",
            "message": "该冻结目标已由管理员跳过",
        }]
        item.finished_at = now
    transition = _record_transition(
        db,
        run=run,
        step_id=step.id,
        event_type=f"item_{action}",
        from_status=previous,
        to_status=ProductionRunStatus.running.value,
        idempotency_key=idempotency_key,
        request_payload=payload,
        actor_type=ProductionActorType.admin,
        actor_id=actor_id,
    )
    await db.flush()
    if action == "retry":
        await _advance_collection_step(db, run=run, step=step)
    else:
        items = (
            await db.execute(
                select(ChapterProductionRunStepItem)
                .where(ChapterProductionRunStepItem.step_id == step.id)
                .order_by(ChapterProductionRunStepItem.id)
                .with_for_update()
            )
        ).scalars().all()
        await _settle_collection_step(db, run=run, step=step, items=items)
    await db.refresh(run)
    transition.result_snapshot = _snapshot(run)
    await db.flush()
    return run, item, False


__all__ = ["advance_run", "confirm_gate", "mutate_step_item", "settle_terminal_task"]
