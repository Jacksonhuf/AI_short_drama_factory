"""Production run 的提交后 outbox 快速投递。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import logging
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.production_runs import ProductionRunTaskBinding
from app.models.task_dispatch_outbox import TaskDispatchOutbox, TaskDispatchOutboxStatus
from app.services.task_dispatch import dispatch_staged_task

logger = logging.getLogger(__name__)


async def dispatch_due_run_outboxes(
    db: AsyncSession,
    *,
    run_id: str,
    dispatcher: Callable[[str], Any] = dispatch_staged_task,
) -> list[str]:
    """查找 run 的 due outbox，并在结束读取事务后逐项快速投递。

    调用方应先提交创建任务、binding 与 outbox 的业务事务。这里在 publish 前
    再次 commit 只读查询事务，确保任何情况下都不会持有未提交事务或数据库锁
    发送 Celery 消息。投递异常不会撤销 outbox，后续 reconciliation 仍可重试。
    """

    now = datetime.now(UTC).replace(tzinfo=None)
    task_ids = list(
        (
            await db.execute(
                select(TaskDispatchOutbox.task_id)
                .join(
                    ProductionRunTaskBinding,
                    ProductionRunTaskBinding.task_id == TaskDispatchOutbox.task_id,
                )
                .where(
                    ProductionRunTaskBinding.run_id == run_id,
                    TaskDispatchOutbox.available_at <= now,
                    or_(
                        TaskDispatchOutbox.status == TaskDispatchOutboxStatus.pending,
                        TaskDispatchOutbox.status == TaskDispatchOutboxStatus.failed,
                    ),
                )
                .order_by(TaskDispatchOutbox.available_at, TaskDispatchOutbox.id)
            )
        ).scalars()
    )
    # SELECT 会开启 autobegin；publish 前结束该读取事务，避免同步 dispatcher
    # 更新同一 outbox 行时与当前异步 session 互相等待。
    await db.commit()
    for task_id in task_ids:
        try:
            dispatcher(task_id)
        except Exception:  # noqa: BLE001
            logger.exception(
                "production run immediate outbox dispatch failed: run_id=%s task_id=%s",
                run_id,
                task_id,
            )
    return task_ids


__all__ = ["dispatch_due_run_outboxes"]
