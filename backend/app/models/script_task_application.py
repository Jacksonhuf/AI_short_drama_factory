"""AI 剧本候选结果的显式应用审计记录。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import TimestampMixin


class ScriptTaskApplication(Base, TimestampMixin):
    """稳定记录一次任务应用，数据库唯一约束保证并发重试不会重复覆盖章节。"""

    __tablename__ = "script_task_applications"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="应用记录 ID")
    task_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("generation_tasks.id", ondelete="RESTRICT"),
        nullable=False,
        comment="已应用的 script_write 任务；一个候选最多应用一次",
    )
    chapter_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("chapters.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="目标章节 ID",
    )
    target_field: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="目标字段：raw_text / condensed_text",
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment="客户端幂等键",
    )
    expected_chapter_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="请求携带的章节版本",
    )
    chapter_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="应用完成后的章节版本",
    )

    __table_args__ = (
        UniqueConstraint("task_id", name="uq_script_task_applications_task_id"),
        UniqueConstraint("idempotency_key", name="uq_script_task_applications_idempotency_key"),
    )
