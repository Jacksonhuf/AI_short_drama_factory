"""统一 Celery 执行入口。

职责：
- Celery 统一只接收业务 task_id；
- 通过 GenerationTask.task_kind + registry 找到具体 WorkerTaskExecutor；
- 回写 executor_type / executor_task_id，便于排障。
"""

from __future__ import annotations

import asyncio
import logging

from celery.result import AsyncResult

from app.core.celery_app import celery_app
from app.core.db_sync import sync_session_maker
from app.core.db import async_session_maker
from app.models.task import GenerationTask
from app.services.task_dispatch import reconcile_stale_task_dispatches
from app.services.task_terminal_notification import notify_task_terminal
from app.services.worker.task_registry import task_executor_registry

logger = logging.getLogger(__name__)


def _record_executor_dispatch(task_id: str, *, executor_type: str, executor_task_id: str | None) -> None:
    """记录兼容 enqueue 入口返回的执行器标识。"""

    with sync_session_maker() as db:
        row = db.get(GenerationTask, task_id)
        if row is None:
            return
        row.executor_type = executor_type
        row.executor_task_id = executor_task_id
        db.commit()


def enqueue_task_execution(task_id: str) -> AsyncResult:
    """兼容既有调用方的提交后直接投递入口。"""

    async_result = run_task_celery.delay(task_id)
    _record_executor_dispatch(
        task_id,
        executor_type="celery",
        executor_task_id=async_result.id,
    )
    return async_result


def revoke_task_execution(task_id: str, *, terminate: bool = True, signal: str = "SIGTERM") -> bool:
    """尽力撤销已记录 executor id 的 Celery 任务。"""

    with sync_session_maker() as db:
        row = db.get(GenerationTask, task_id)
        if row is None:
            return False
        if (row.executor_type or "").strip() != "celery":
            return False
        executor_task_id = (row.executor_task_id or "").strip()
        if not executor_task_id:
            return False

    try:
        AsyncResult(executor_task_id, app=celery_app).revoke(terminate=terminate, signal=signal)
    except Exception:  # noqa: BLE001
        logger.exception("failed to revoke celery task: task_id=%s executor_task_id=%s", task_id, executor_task_id)
        return False
    return True


@celery_app.task(name="task.execute")
def run_task_celery(task_id: str) -> None:
    """按 task_kind 执行业务任务，并在已提交终态后触发统一通知。"""

    with sync_session_maker() as db:
        row = db.get(GenerationTask, task_id)
        if row is None:
            return
        if str(getattr(row.status, "value", row.status)) in {"succeeded", "failed", "cancelled"}:
            notify_task_terminal(task_id, session_maker=sync_session_maker)
            return
        task_kind = (row.task_kind or "").strip() or str((row.payload or {}).get("task_kind") or "").strip()
    executor = task_executor_registry.resolve(task_kind)
    try:
        executor.run(task_id)
    finally:
        notify_task_terminal(task_id, session_maker=sync_session_maker)


@celery_app.task(name="task.dispatch.reconcile")
def reconcile_task_dispatch_outbox(stale_after_seconds: int = 30, batch_size: int = 100) -> int:
    """补投递 stale outbox，供 Celery beat 或运维命令按需调用。"""

    results = reconcile_stale_task_dispatches(
        stale_after_seconds=stale_after_seconds,
        batch_size=batch_size,
        session_maker=sync_session_maker,
    )
    return sum(1 for result in results if result.dispatched)


@celery_app.task(name="production.run.reconcile")
def reconcile_production_run_workflows(batch_size: int = 100) -> int:
    """运行可重复的 production 终态通知和停滞步骤对账。"""

    async def reconcile() -> int:
        from app.services.production_runs.reconciliation import reconcile_production_runs

        async with async_session_maker() as db:
            repaired = await reconcile_production_runs(db, batch_size=batch_size)
            await db.commit()
            return repaired

    return asyncio.run(reconcile())
