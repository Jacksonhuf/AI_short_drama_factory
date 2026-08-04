"""可靠任务投递与终态通知基础设施测试。"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.db import Base
from app.core.celery_app import celery_app
from app.models.task import GenerationTask
from app.models.task_dispatch_outbox import TaskDispatchOutbox, TaskDispatchOutboxStatus
from app.services.task_dispatch import (
    TaskOutboxDispatcher,
    _utcnow,
    reconcile_stale_task_dispatches,
    stage_task_dispatch,
)
from app.services.task_terminal_notification import (
    notify_task_terminal,
    terminal_notification_registry,
)


def _task(task_id: str, *, status: str = "pending") -> GenerationTask:
    """构造可供 dispatcher 测试的最小业务任务。"""

    return GenerationTask(
        id=task_id,
        mode="async_polling",
        task_kind="shot_frame_prompt",
        status=status,
        progress=0,
        payload={"task_kind": "shot_frame_prompt", "run_args": {}},
        result=None,
        error="",
    )


@pytest.mark.asyncio
async def test_stage_task_dispatch_is_idempotent_in_creation_transaction(tmp_path) -> None:
    """重复 staging 同一 task_id 只产生一个 outbox。"""

    db_path = tmp_path / "stage-outbox.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    session_local = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_local() as db:
        db.add(_task("task-stage"))
        await db.flush()
        first = await stage_task_dispatch(db, "task-stage")
        second = await stage_task_dispatch(db, "task-stage")
        await db.commit()

        assert first.id == second.id
        rows = (await db.execute(TaskDispatchOutbox.__table__.select())).all()
        assert len(rows) == 1

    await engine.dispose()


def test_dispatcher_publishes_once_and_records_executor(tmp_path) -> None:
    """重复 dispatcher 调用不会再次发布已 dispatched 的记录。"""

    db_path = tmp_path / "dispatch-once.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    session_local = sessionmaker(engine, class_=Session, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with session_local() as db:
        db.add(_task("task-dispatch"))
        db.add(
            TaskDispatchOutbox(
                id="outbox-dispatch",
                task_id="task-dispatch",
                status=TaskDispatchOutboxStatus.pending,
                attempt=0,
                available_at=_utcnow(),
            )
        )
        db.commit()

    published: list[tuple[str, str]] = []

    def _publish(task_id: str, executor_task_id: str) -> SimpleNamespace:
        published.append((task_id, executor_task_id))
        return SimpleNamespace(id=executor_task_id)

    dispatcher = TaskOutboxDispatcher(session_maker=session_local, publisher=_publish)
    first = dispatcher.dispatch_task("task-dispatch")
    second = dispatcher.dispatch_task("task-dispatch")

    assert first.dispatched is True
    assert second.skipped is True
    assert published == [("task-dispatch", "generation-task-task-dispatch")]
    with session_local() as db:
        task = db.get(GenerationTask, "task-dispatch")
        outbox = db.get(TaskDispatchOutbox, "outbox-dispatch")
        assert task is not None
        assert task.executor_task_id == "generation-task-task-dispatch"
        assert outbox is not None
        assert outbox.status == TaskDispatchOutboxStatus.dispatched
        assert outbox.attempt == 1

    engine.dispose()


def test_dispatcher_failure_records_backoff_for_retry(tmp_path) -> None:
    """Broker 异常会持久化失败信息与下一次退避时间，不丢失投递意图。"""

    db_path = tmp_path / "dispatch-backoff.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    session_local = sessionmaker(engine, class_=Session, expire_on_commit=False)
    Base.metadata.create_all(engine)
    available_at = _utcnow()
    with session_local() as db:
        db.add(_task("task-backoff"))
        db.add(
            TaskDispatchOutbox(
                id="outbox-backoff",
                task_id="task-backoff",
                status=TaskDispatchOutboxStatus.pending,
                attempt=0,
                available_at=available_at,
            )
        )
        db.commit()

    def _fail_publish(_task_id: str, _executor_task_id: str) -> None:
        raise ConnectionError("broker unavailable")

    result = TaskOutboxDispatcher(
        session_maker=session_local,
        publisher=_fail_publish,
        base_retry_seconds=5,
    ).dispatch_task("task-backoff")

    assert result.dispatched is False
    assert result.error == "broker unavailable"
    with session_local() as db:
        outbox = db.get(TaskDispatchOutbox, "outbox-backoff")
        assert outbox is not None
        assert outbox.status == TaskDispatchOutboxStatus.failed
        assert outbox.attempt == 1
        assert outbox.available_at >= available_at + timedelta(seconds=5)
        assert outbox.last_error == "broker unavailable"

    engine.dispose()


def test_reconciliation_redelivers_stale_pending_outbox(tmp_path) -> None:
    """Reconciler 会补投递 stale pending 记录，并复用统一 dispatcher。"""

    db_path = tmp_path / "reconcile-outbox.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    session_local = sessionmaker(engine, class_=Session, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with session_local() as db:
        db.add(_task("task-reconcile"))
        db.add(
            TaskDispatchOutbox(
                id="outbox-reconcile",
                task_id="task-reconcile",
                status=TaskDispatchOutboxStatus.pending,
                attempt=0,
                available_at=_utcnow() - timedelta(seconds=1),
                updated_at=_utcnow() - timedelta(seconds=60),
            )
        )
        db.commit()

    published: list[str] = []

    def _publish(task_id: str, executor_task_id: str) -> SimpleNamespace:
        published.append(task_id)
        return SimpleNamespace(id=executor_task_id)

    dispatcher = TaskOutboxDispatcher(session_maker=session_local, publisher=_publish)
    results = reconcile_stale_task_dispatches(
        stale_after_seconds=30,
        session_maker=session_local,
        dispatcher=dispatcher,
    )

    assert [result.task_id for result in results if result.dispatched] == ["task-reconcile"]
    assert published == ["task-reconcile"]
    engine.dispose()


def test_default_terminal_notification_log_is_process_idempotent(tmp_path, caplog) -> None:
    """无 production binding 时，同一终态任务只输出一次扩展点日志。"""

    db_path = tmp_path / "terminal-notification.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    session_local = sessionmaker(engine, class_=Session, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with session_local() as db:
        db.add(_task("task-terminal", status="succeeded"))
        db.commit()

    terminal_notification_registry.unbind()
    with caplog.at_level("INFO"):
        assert notify_task_terminal("task-terminal", session_maker=session_local) is True
        assert notify_task_terminal("task-terminal", session_maker=session_local) is False

    messages = [record.message for record in caplog.records if "no production binding" in record.message]
    assert len(messages) == 1
    terminal_notification_registry.unbind()
    engine.dispose()


def test_celery_beat_schedules_dispatch_and_workflow_reconciliation() -> None:
    """生产调度必须周期补偿 outbox 和工作流通知，不能只依赖请求内即时投递。"""

    schedule = celery_app.conf.beat_schedule
    assert schedule["reconcile-task-dispatch-outbox"]["task"] == "task.dispatch.reconcile"
    assert schedule["reconcile-task-dispatch-outbox"]["schedule"] == 30.0
    assert schedule["reconcile-production-runs"]["task"] == "production.run.reconcile"
    assert schedule["reconcile-production-runs"]["schedule"] == 30.0
