"""Production run 对账恢复服务。"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.production_runs import (
    ChapterProductionRun,
    ChapterProductionRunStep,
    ProductionRunStatus,
    ProductionRunTaskBinding,
    ProductionStepStatus,
)
from app.models.task import GenerationTask, GenerationTaskStatus
from app.services.production_runs.orchestration import advance_run, settle_terminal_task


async def reconcile_production_runs(
    db: AsyncSession,
    *,
    batch_size: int = 100,
) -> int:
    """修复终态通知丢失及 running 但无可用活动任务的线性运行。

    每项操作依赖 binding 与步骤状态实现持久化幂等，重复扫描不会创建重复任务。
    """

    terminal_task_ids = (
        await db.execute(
            select(ProductionRunTaskBinding.task_id)
            .join(
                GenerationTask,
                GenerationTask.id == ProductionRunTaskBinding.task_id,
            )
            .join(
                ChapterProductionRunStep,
                ChapterProductionRunStep.id == ProductionRunTaskBinding.step_id,
            )
            .where(
                GenerationTask.status.in_(
                    [
                        GenerationTaskStatus.succeeded.value,
                        GenerationTaskStatus.failed.value,
                        GenerationTaskStatus.cancelled.value,
                    ]
                ),
                or_(
                    ProductionRunTaskBinding.notified_at.is_(None),
                    ChapterProductionRunStep.status == ProductionStepStatus.running.value,
                ),
            )
            .order_by(ProductionRunTaskBinding.created_at)
            .limit(batch_size)
        )
    ).scalars().all()
    repaired = 0
    for task_id in terminal_task_ids:
        if await settle_terminal_task(db, task_id):
            repaired += 1

    remaining = max(batch_size - repaired, 0)
    if remaining:
        running_ids = (
            await db.execute(
                select(ChapterProductionRun.id)
                .where(
                    ChapterProductionRun.status == ProductionRunStatus.running.value,
                    ChapterProductionRun.cancel_requested.is_(False),
                )
                .order_by(ChapterProductionRun.updated_at)
                .limit(remaining)
            )
        ).scalars().all()
        for run_id in running_ids:
            if await advance_run(db, run_id):
                repaired += 1
    return repaired


__all__ = ["reconcile_production_runs"]
