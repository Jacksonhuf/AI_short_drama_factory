"""任务投递 outbox ORM。

该表把业务任务创建与“待投递”事实放入同一数据库事务，避免 Web 进程在
提交 GenerationTask 后、发送 Celery 消息前退出而永久丢失任务。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin


class TaskDispatchOutboxStatus(str, Enum):
    """任务投递 outbox 的持久化状态。"""

    pending = "pending"
    dispatched = "dispatched"
    failed = "failed"


class TaskDispatchOutbox(Base, TimestampMixin):
    """记录一个 GenerationTask 的唯一、可恢复投递意图。"""

    __tablename__ = "task_dispatch_outbox"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="Outbox ID")
    task_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("generation_tasks.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment="业务任务 ID；每个任务最多一个投递意图",
    )
    status: Mapped[TaskDispatchOutboxStatus] = mapped_column(
        String(32),
        nullable=False,
        default=TaskDispatchOutboxStatus.pending,
        comment="投递状态：pending / dispatched / failed",
    )
    attempt: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="已尝试投递次数",
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="下一次允许投递的时间",
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="成功投递时间",
    )
    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="最近一次投递错误",
    )

    __table_args__ = (
        Index("ix_task_dispatch_outbox_status_available_at", "status", "available_at"),
    )
