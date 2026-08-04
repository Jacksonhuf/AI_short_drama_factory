"""帧提示词与视频任务的可复用创建服务。

服务只在调用方事务中创建 GenerationTask、业务关联和 outbox，不提交事务或直接
调用 Celery，供 HTTP route 与 production-run 编排共享。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.task_manager import DeliveryMode, SqlAlchemyTaskStore, TaskManager
from app.models.task_links import GenerationTaskLink
from app.services.film.generated_video import build_run_args as build_video_run_args
from app.services.film.shot_frame_prompt_tasks import (
    build_run_args as build_frame_prompt_run_args,
    normalize_frame_type,
    relation_type_for_frame,
)
from app.services.script_processing_tasks import AsyncTaskCreateResult
from app.services.studio.shot_status import mark_shot_generating
from app.services.task_dispatch import stage_task_dispatch


class _CreateOnlyTask:
    """仅向 TaskManager 提供异步任务记录所需的最小接口。"""

    async def run(self, *args: object, **kwargs: object) -> None:
        """任务实际由已注册的 worker executor 执行。"""

    async def status(self) -> dict[str, object]:
        """创建阶段没有运行时状态。"""

        return {}

    async def is_done(self) -> bool:
        """新建异步任务尚未完成。"""

        return False

    async def get_result(self) -> object:
        """新建异步任务尚无结果。"""

        return None


async def create_frame_prompt_task(
    db: AsyncSession,
    *,
    shot_id: str,
    frame_type: str,
) -> AsyncTaskCreateResult:
    """原子暂存单镜头帧提示词任务、关联和 outbox。"""

    normalized = normalize_frame_type(frame_type)
    relation_type = relation_type_for_frame(normalized)
    task = await TaskManager(
        store=SqlAlchemyTaskStore(db),
        strategies={},
    ).create(
        task=_CreateOnlyTask(),
        mode=DeliveryMode.async_polling,
        task_kind="shot_frame_prompt",
        run_args=await build_frame_prompt_run_args(
            db,
            shot_id=shot_id,
            frame_type=normalized,
        ),
    )
    db.add(
        GenerationTaskLink(
            task_id=task.id,
            resource_type="prompt",
            relation_type=relation_type,
            relation_entity_id=shot_id,
        )
    )
    await mark_shot_generating(db, shot_id=shot_id)
    await stage_task_dispatch(db, task.id)
    return AsyncTaskCreateResult(
        task_id=task.id,
        status=task.status,
        reused=False,
        relation_type=relation_type,
        relation_entity_id=shot_id,
    )


async def create_video_task(
    db: AsyncSession,
    *,
    shot_id: str,
    reference_mode: str,
    ratio: str,
    prompt: str | None = None,
    images: list[str] | None = None,
) -> AsyncTaskCreateResult:
    """原子暂存视频任务、结果回流关联和 outbox。"""

    task = await TaskManager(
        store=SqlAlchemyTaskStore(db),
        strategies={},
    ).create(
        task=_CreateOnlyTask(),
        mode=DeliveryMode.async_polling,
        task_kind="video_generation",
        run_args=await build_video_run_args(
            db,
            shot_id=shot_id,
            reference_mode=reference_mode,
            prompt=prompt,
            images=images or [],
            ratio=ratio,
        ),
    )
    db.add(
        GenerationTaskLink(
            task_id=task.id,
            resource_type="video",
            relation_type="video",
            relation_entity_id=shot_id,
        )
    )
    await mark_shot_generating(db, shot_id=shot_id)
    await stage_task_dispatch(db, task.id)
    return AsyncTaskCreateResult(
        task_id=task.id,
        status=task.status,
        reused=False,
        relation_type="video",
        relation_entity_id=shot_id,
    )


__all__ = ["create_frame_prompt_task", "create_video_task"]
