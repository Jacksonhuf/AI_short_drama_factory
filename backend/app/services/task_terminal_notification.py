"""任务终态通知扩展点。

当前尚无 production workflow binding，因此默认 hook 仅输出一次结构化日志。
后续编排层可绑定持久化、事务幂等的 hook，而无需修改 Celery 执行入口。
"""

from __future__ import annotations

import logging
import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from threading import Thread

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.db_sync import sync_session_maker
from app.models.task import GenerationTask, GenerationTaskStatus
from app.models.production_runs import ProductionRunTaskBinding

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {
    GenerationTaskStatus.succeeded.value,
    GenerationTaskStatus.failed.value,
    GenerationTaskStatus.cancelled.value,
}


@dataclass(frozen=True, slots=True)
class TerminalTaskNotification:
    """交给终态 hook 的最小稳定任务快照。"""

    task_id: str
    task_kind: str
    status: str


class TerminalNotificationRegistry:
    """保存 production hook，并为默认日志提供进程内幂等保护。"""

    def __init__(self) -> None:
        self._hook: Callable[[TerminalTaskNotification], None] | None = None
        self._logged_task_ids: set[str] = set()
        self._lock = Lock()

    def bind(self, hook: Callable[[TerminalTaskNotification], None]) -> None:
        """绑定终态消费者；消费者必须以 task_id 实现持久化幂等。"""

        with self._lock:
            self._hook = hook

    def unbind(self) -> None:
        """移除 production hook，恢复默认幂等日志行为。"""

        with self._lock:
            self._hook = None
            self._logged_task_ids.clear()

    def notify(self, notification: TerminalTaskNotification) -> bool:
        """调用已绑定 hook，或对同一 task_id 至多输出一次默认日志。"""

        with self._lock:
            hook = self._hook
            if hook is None:
                if notification.task_id in self._logged_task_ids:
                    return False
                self._logged_task_ids.add(notification.task_id)

        if hook is not None:
            hook(notification)
            return True

        logger.info(
            "task terminal notification has no production binding: task_id=%s task_kind=%s status=%s",
            notification.task_id,
            notification.task_kind,
            notification.status,
        )
        return True


terminal_notification_registry = TerminalNotificationRegistry()


def _async_driver_name(drivername: str) -> str:
    """把 worker 同步连接驱动映射为编排 service 使用的异步驱动。"""

    if drivername == "sqlite":
        return "sqlite+aiosqlite"
    if drivername in {"mysql", "mysql+pymysql"}:
        return "mysql+aiomysql"
    return drivername


def _settle_production_binding(
    task_id: str,
    *,
    run_id: str,
    session_maker: sessionmaker[Session],
) -> bool:
    """结算 binding，提交后立即投递推进步骤创建的 due outbox。"""

    bind = session_maker.kw.get("bind")
    if bind is None:
        return False
    async_engine = create_async_engine(
        bind.url.set(drivername=_async_driver_name(bind.url.drivername)),
        future=True,
    )
    async_maker = async_sessionmaker(
        async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async def settle() -> bool:
        from app.services.production_runs.dispatch import dispatch_due_run_outboxes
        from app.services.production_runs.orchestration import settle_terminal_task
        from app.services.task_dispatch import TaskOutboxDispatcher

        try:
            async with async_maker() as db:
                changed = await settle_terminal_task(db, task_id)
                await db.commit()
                dispatcher = TaskOutboxDispatcher(
                    session_maker=session_maker
                ).dispatch_task
                await dispatch_due_run_outboxes(
                    db,
                    run_id=run_id,
                    dispatcher=dispatcher,
                )
                return changed
        finally:
            await async_engine.dispose()

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(settle())

    result: list[bool] = []
    error: list[BaseException] = []

    def run_in_thread() -> None:
        """在 API 事件循环外运行临时异步数据库事务。"""

        try:
            result.append(asyncio.run(settle()))
        except BaseException as exc:  # noqa: BLE001
            error.append(exc)

    thread = Thread(target=run_in_thread, daemon=False)
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return bool(result and result[0])


def notify_task_terminal(
    task_id: str,
    *,
    session_maker: sessionmaker[Session] = sync_session_maker,
) -> bool:
    """读取已提交任务终态并通知 hook；非终态、缺失或 hook 异常均不影响执行器。"""

    with session_maker() as db:
        task = db.get(GenerationTask, task_id)
        if task is None:
            return False
        status = str(getattr(task.status, "value", task.status))
        if status not in TERMINAL_STATUSES:
            return False
        notification = TerminalTaskNotification(
            task_id=task.id,
            task_kind=task.task_kind,
            status=status,
        )
        production_binding = (
            db.query(ProductionRunTaskBinding.run_id)
            .filter(ProductionRunTaskBinding.task_id == task_id)
            .first()
        )

    try:
        if production_binding is not None:
            _settle_production_binding(
                task_id,
                run_id=str(production_binding[0]),
                session_maker=session_maker,
            )
        return terminal_notification_registry.notify(notification)
    except Exception:  # noqa: BLE001
        logger.exception("task terminal notification hook failed: task_id=%s", task_id)
        return False
