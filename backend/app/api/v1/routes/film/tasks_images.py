from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.dependencies import get_db
from app.schemas.common import ApiResponse, created_response
from app.services.film.generation_task_creation import create_frame_prompt_task
from app.services.task_dispatch import dispatch_staged_task

from .common import (
    ShotFramePromptRequest,
    TaskCreated,
)
router = APIRouter()


@router.post(
    "/tasks/shot-frame-prompts",
    response_model=ApiResponse[TaskCreated],
    status_code=201,
    summary="镜头分镜帧提示词生成（任务版）",
)
async def create_shot_frame_prompt_task(
    body: ShotFramePromptRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[TaskCreated]:
    """原子创建帧提示词任务及 outbox，并在提交后尝试立即投递。"""

    task = await create_frame_prompt_task(
        db,
        shot_id=body.shot_id,
        frame_type=body.frame_type,
    )
    await db.commit()

    dispatch_staged_task(task.task_id)
    return created_response(TaskCreated(task_id=task.task_id))
