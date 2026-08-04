"""章节生产运行、步骤、任务绑定与状态迁移审计 ORM。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import TimestampMixin

if TYPE_CHECKING:
    from app.models.studio_projects import Chapter, Project


class ProductionRunStatus(str, Enum):
    """章节生产运行状态。"""

    draft = "draft"
    running = "running"
    waiting_human = "waiting_human"
    paused = "paused"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class ProductionStepStatus(str, Enum):
    """生产步骤及 fan-out 项共享的状态集合。"""

    pending = "pending"
    running = "running"
    waiting = "waiting"
    partial = "partial"
    succeeded = "succeeded"
    failed = "failed"
    skipped = "skipped"
    cancelled = "cancelled"


class ProductionExecutionMode(str, Enum):
    """manifest 步骤执行形态。"""

    linear = "linear"
    fan_out = "fan_out"
    barrier = "barrier"
    gate = "gate"


class ProductionStage(str, Enum):
    """manifest v1 支持的稳定阶段标识。"""

    script_write = "script_write"
    script_review_gate = "script_review_gate"
    script_simplify = "script_simplify"
    script_consistency = "script_consistency"
    script_optimize = "script_optimize"
    script_divide = "script_divide"
    script_extract = "script_extract"
    human_preparation_gate = "human_preparation_gate"
    frame_prompt = "frame_prompt"
    frame_image = "frame_image"
    video_readiness = "video_readiness"
    video_submit_gate = "video_submit_gate"
    video_generation = "video_generation"


class ProductionBindingRole(str, Enum):
    """工作流任务在一次步骤尝试中的角色。"""

    primary = "primary"
    child = "child"


class ProductionActorType(str, Enum):
    """触发状态迁移的主体类型。"""

    admin = "admin"
    system = "system"
    worker = "worker"


class ChapterProductionRun(Base, TimestampMixin):
    """章节级生产运行，是编排状态的唯一持久化真相。"""

    __tablename__ = "chapter_production_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    chapter_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False
    )
    preset_key: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_version: Mapped[str] = mapped_column(String(32), nullable=False)
    manifest_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    target_snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[ProductionRunStatus] = mapped_column(
        String(32), nullable=False, default=ProductionRunStatus.draft
    )
    active_chapter_id: Mapped[str | None] = mapped_column(
        String(64),
        Computed(
            "CASE WHEN status IN "
            "('draft','running','waiting_human','paused','failed') "
            "THEN chapter_id ELSE NULL END",
            persisted=True,
        ),
        nullable=True,
        comment="活跃状态下等于 chapter_id，用于跨数据库单活跃约束",
    )
    current_step_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lock_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    transition_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped["Project"] = relationship()
    chapter: Mapped["Chapter"] = relationship(back_populates="production_runs")
    steps: Mapped[list["ChapterProductionRunStep"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )
    bindings: Mapped[list["ProductionRunTaskBinding"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )
    transitions: Mapped[list["ProductionRunTransition"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        UniqueConstraint("active_chapter_id", name="uq_chapter_production_runs_active_chapter"),
        Index("ix_chapter_production_runs_chapter_created", "chapter_id", "created_at"),
        Index("ix_chapter_production_runs_status_updated", "status", "updated_at"),
        Index(
            "ix_chapter_production_runs_reconcile",
            "status",
            "last_reconciled_at",
            "updated_at",
        ),
    )


class ChapterProductionRunStep(Base, TimestampMixin):
    """版本化 manifest 展开后的一项持久化步骤。"""

    __tablename__ = "chapter_production_run_steps"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("chapter_production_runs.id", ondelete="CASCADE"), nullable=False
    )
    stage_key: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1")
    execution_mode: Mapped[ProductionExecutionMode] = mapped_column(String(32), nullable=False)
    status: Mapped[ProductionStepStatus] = mapped_column(
        String(32), nullable=False, default=ProductionStepStatus.pending
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    target_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    output_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    blocked_reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    gate_snapshot_hash: Mapped[str | None] = mapped_column(String(64))
    confirmed_by: Mapped[str | None] = mapped_column(String(64))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lock_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[ChapterProductionRun] = relationship(back_populates="steps")
    items: Mapped[list["ChapterProductionRunStepItem"]] = relationship(
        back_populates="step", cascade="all, delete-orphan", passive_deletes=True
    )
    bindings: Mapped[list["ProductionRunTaskBinding"]] = relationship(back_populates="step")

    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_production_run_steps_sequence"),
        UniqueConstraint(
            "run_id", "idempotency_key", name="uq_production_run_steps_idempotency"
        ),
        Index("ix_production_run_steps_run_status_sequence", "run_id", "status", "sequence"),
    )


class ChapterProductionRunStepItem(Base, TimestampMixin):
    """fan-out 步骤冻结的单一业务目标。"""

    __tablename__ = "chapter_production_run_step_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    step_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("chapter_production_run_steps.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    target_key: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_version: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[ProductionStepStatus] = mapped_column(
        String(32), nullable=False, default=ProductionStepStatus.pending
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    blocked_reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    output_ref: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    lock_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    step: Mapped[ChapterProductionRunStep] = relationship(back_populates="items")
    bindings: Mapped[list["ProductionRunTaskBinding"]] = relationship(back_populates="item")

    __table_args__ = (
        UniqueConstraint("step_id", "target_key", name="uq_production_step_items_target"),
        UniqueConstraint(
            "step_id", "idempotency_key", name="uq_production_step_items_idempotency"
        ),
        Index("ix_production_step_items_step_status", "step_id", "status"),
        Index("ix_production_step_items_entity", "entity_type", "entity_id"),
    )


class ProductionRunTaskBinding(Base, TimestampMixin):
    """GenerationTask 与工作流尝试的不可覆盖历史绑定。"""

    __tablename__ = "production_run_task_bindings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("chapter_production_runs.id", ondelete="CASCADE"), nullable=False
    )
    step_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("chapter_production_run_steps.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("chapter_production_run_step_items.id", ondelete="CASCADE"),
        nullable=True,
    )
    task_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("generation_tasks.id", ondelete="RESTRICT"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    task_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    binding_role: Mapped[ProductionBindingRole] = mapped_column(String(32), nullable=False)
    terminal_status: Mapped[str | None] = mapped_column(String(32))
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[ChapterProductionRun] = relationship(back_populates="bindings")
    step: Mapped[ChapterProductionRunStep] = relationship(back_populates="bindings")
    item: Mapped[ChapterProductionRunStepItem | None] = relationship(back_populates="bindings")

    __table_args__ = (
        UniqueConstraint("task_id", name="uq_production_run_task_bindings_task"),
        UniqueConstraint(
            "step_id",
            "item_id",
            "attempt",
            "task_kind",
            name="uq_production_run_task_bindings_attempt",
        ),
        Index("ix_production_run_task_bindings_run_step", "run_id", "step_id"),
        Index("ix_production_run_task_bindings_task_notified", "task_id", "notified_at"),
    )


class ProductionRunTransition(Base):
    """一次状态变更的幂等记录和审计事件。"""

    __tablename__ = "production_run_transitions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("chapter_production_runs.id", ondelete="CASCADE"), nullable=False
    )
    step_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("chapter_production_run_steps.id", ondelete="SET NULL"),
        nullable=True,
    )
    transition_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_type: Mapped[ProductionActorType] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    event_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    result_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    run: Mapped[ChapterProductionRun] = relationship(back_populates="transitions")

    __table_args__ = (
        UniqueConstraint(
            "run_id", "transition_version", name="uq_production_run_transitions_version"
        ),
        UniqueConstraint(
            "run_id", "idempotency_key", name="uq_production_run_transitions_idempotency"
        ),
        Index("ix_production_run_transitions_run_created", "run_id", "created_at"),
    )


__all__ = [
    "ChapterProductionRun",
    "ChapterProductionRunStep",
    "ChapterProductionRunStepItem",
    "ProductionActorType",
    "ProductionBindingRole",
    "ProductionExecutionMode",
    "ProductionRunStatus",
    "ProductionRunTaskBinding",
    "ProductionRunTransition",
    "ProductionStage",
    "ProductionStepStatus",
]
