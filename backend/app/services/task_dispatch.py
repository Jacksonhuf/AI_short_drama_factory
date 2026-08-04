"""GenerationTask 的可靠投递服务。

Web 事务通过 ``stage_task_dispatch`` 写入投递意图；事务提交后由
``TaskOutboxDispatcher`` 发送 Celery 消息。Reconciler 可重复扫描并补投递，
而 task_id 唯一约束、行锁和确定性 Celery task id 共同限制重复投递。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, sessionmaker

from app.core.db_sync import sync_session_maker
from app.models.task import GenerationTask, GenerationTaskStatus
from app.models.task_dispatch_outbox import TaskDispatchOutbox, TaskDispatchOutboxStatus

logger = logging.getLogger(__name__)

TERMINAL_TASK_STATUSES = {
    GenerationTaskStatus.succeeded.value,
    GenerationTaskStatus.failed.value,
    GenerationTaskStatus.cancelled.value,
}


@dataclass(frozen=True, slots=True)
class TaskDispatchResult:
    """一次 outbox 投递尝试的结果。"""

    task_id: str
    dispatched: bool
    skipped: bool = False
    executor_task_id: str | None = None
    error: str | None = None


def _utcnow() -> datetime:
    """返回与现有 MySQL/SQLite ORM 字段兼容的无时区 UTC 时间。"""

    return datetime.now(UTC).replace(tzinfo=None)


def _status_value(value: object) -> str:
    """统一提取 SQLAlchemy 枚举字段的字符串值。"""

    return str(getattr(value, "value", value))


def _executor_task_id(task_id: str) -> str:
    """生成稳定的 Celery 消息 ID，使同一业务任务重投时保持同一执行标识。"""

    return f"generation-task-{task_id}"


def _publish_celery_task(task_id: str, executor_task_id: str) -> Any:
    """通过统一 Celery 入口发布任务，并显式使用稳定消息 ID。"""

    from app.tasks.execute_task import run_task_celery

    return run_task_celery.apply_async(args=[task_id], task_id=executor_task_id)


async def stage_task_dispatch(db: AsyncSession, task_id: str) -> TaskDispatchOutbox:
    """在调用方事务中幂等写入一个任务投递意图。

    函数只 flush、不 commit；因此 GenerationTask、业务关联和 outbox 可以由
    调用方在同一事务中原子提交。
    """

    existing = (
        await db.execute(
            select(TaskDispatchOutbox)
            .where(TaskDispatchOutbox.task_id == task_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    row = TaskDispatchOutbox(
        id=uuid.uuid4().hex,
        task_id=task_id,
        status=TaskDispatchOutboxStatus.pending,
        attempt=0,
        available_at=_utcnow(),
    )
    db.add(row)
    await db.flush()
    return row


class TaskOutboxDispatcher:
    """同步 outbox dispatcher，供 Web 提交后快速投递和 Reconciler 共用。"""

    def __init__(
        self,
        *,
        session_maker: sessionmaker[Session] = sync_session_maker,
        publisher: Callable[[str, str], Any] = _publish_celery_task,
        base_retry_seconds: int = 5,
        max_retry_seconds: int = 300,
    ) -> None:
        self._session_maker = session_maker
        self._publisher = publisher
        self._base_retry_seconds = max(1, base_retry_seconds)
        self._max_retry_seconds = max(self._base_retry_seconds, max_retry_seconds)

    def dispatch_task(self, task_id: str) -> TaskDispatchResult:
        """锁定并投递指定任务的 due outbox；重复调用已投递记录会直接跳过。"""

        now = _utcnow()
        with self._session_maker() as db:
            row = self._load_for_update(db, task_id)
            if row is None:
                return TaskDispatchResult(task_id=task_id, dispatched=False, skipped=True)
            if _status_value(row.status) == TaskDispatchOutboxStatus.dispatched.value:
                return TaskDispatchResult(
                    task_id=task_id,
                    dispatched=False,
                    skipped=True,
                    executor_task_id=self._current_executor_id(db, task_id),
                )
            if row.available_at > now:
                return TaskDispatchResult(task_id=task_id, dispatched=False, skipped=True)

            task = db.get(GenerationTask, task_id)
            if task is None or _status_value(task.status) in TERMINAL_TASK_STATUSES:
                row.status = TaskDispatchOutboxStatus.dispatched
                row.dispatched_at = now
                row.last_error = None
                db.commit()
                return TaskDispatchResult(task_id=task_id, dispatched=False, skipped=True)

            executor_task_id = _executor_task_id(task_id)
            row.attempt += 1
            try:
                result = self._publisher(task_id, executor_task_id)
                published_id = str(getattr(result, "id", "") or executor_task_id)
                row.status = TaskDispatchOutboxStatus.dispatched
                row.dispatched_at = now
                row.last_error = None
                task.executor_type = "celery"
                task.executor_task_id = published_id
                db.commit()
                return TaskDispatchResult(
                    task_id=task_id,
                    dispatched=True,
                    executor_task_id=published_id,
                )
            except Exception as exc:  # noqa: BLE001
                row.status = TaskDispatchOutboxStatus.failed
                row.last_error = str(exc)
                row.available_at = now + timedelta(seconds=self._retry_delay(row.attempt))
                db.commit()
                logger.exception("task outbox dispatch failed: task_id=%s attempt=%s", task_id, row.attempt)
                return TaskDispatchResult(
                    task_id=task_id,
                    dispatched=False,
                    executor_task_id=executor_task_id,
                    error=str(exc),
                )

    @staticmethod
    def _load_for_update(db: Session, task_id: str) -> TaskDispatchOutbox | None:
        """按 task_id 锁定 outbox，阻止多个 dispatcher 并发发送同一记录。"""

        return db.execute(
            select(TaskDispatchOutbox)
            .where(TaskDispatchOutbox.task_id == task_id)
            .with_for_update()
        ).scalar_one_or_none()

    @staticmethod
    def _current_executor_id(db: Session, task_id: str) -> str | None:
        """读取已投递任务的 executor id，供幂等返回与诊断使用。"""

        task = db.get(GenerationTask, task_id)
        return task.executor_task_id if task is not None else None

    def _retry_delay(self, attempt: int) -> int:
        """按指数退避计算下一次投递间隔，并限制最大等待时间。"""

        return min(self._max_retry_seconds, self._base_retry_seconds * (2 ** max(0, attempt - 1)))


def dispatch_staged_task(task_id: str) -> TaskDispatchResult:
    """使用默认 dispatcher 投递一个已提交的 outbox。"""

    return TaskOutboxDispatcher().dispatch_task(task_id)


def reconcile_stale_task_dispatches(
    *,
    stale_after_seconds: int = 30,
    batch_size: int = 100,
    session_maker: sessionmaker[Session] = sync_session_maker,
    dispatcher: TaskOutboxDispatcher | None = None,
) -> list[TaskDispatchResult]:
    """扫描 stale pending/到期 failed outbox 并补投递。

    候选查询只取 task_id；真正投递时会再次加行锁并校验状态，因此多个
    Reconciler 并发扫描同一批候选也是安全的。
    """

    now = _utcnow()
    stale_before = now - timedelta(seconds=max(0, stale_after_seconds))
    with session_maker() as db:
        task_ids = list(
            db.execute(
                select(TaskDispatchOutbox.task_id)
                .where(
                    TaskDispatchOutbox.available_at <= now,
                    or_(
                        (
                            (TaskDispatchOutbox.status == TaskDispatchOutboxStatus.pending)
                            & (TaskDispatchOutbox.updated_at <= stale_before)
                        ),
                        TaskDispatchOutbox.status == TaskDispatchOutboxStatus.failed,
                    ),
                )
                .order_by(TaskDispatchOutbox.available_at, TaskDispatchOutbox.id)
                .limit(max(1, batch_size))
            ).scalars()
        )

    active_dispatcher = dispatcher or TaskOutboxDispatcher(session_maker=session_maker)
    return [active_dispatcher.dispatch_task(task_id) for task_id in task_ids]
