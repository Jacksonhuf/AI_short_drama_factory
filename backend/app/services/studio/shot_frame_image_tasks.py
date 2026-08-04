"""镜头帧图片任务的业务创建服务。

该模块承接原 route 中的提示词渲染、帧槽定位和图片任务创建，使 production run
无需内部 HTTP 调用即可复用相同能力。
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.contracts.image_generation import ImageResolutionProfile, ImageTargetRatio
from app.models.studio import ShotDetail, ShotFrameImage, ShotFrameType
from app.models.task import GenerationTask
from app.schemas.studio.shots import ShotLinkedAssetItem
from app.services.film.shot_frame_prompt_tasks import build_run_args as build_prompt_run_args
from app.services.script_processing_tasks import AsyncTaskCreateResult
from app.services.studio.generation.frame import (
    build_frame_base_draft,
    build_frame_context,
    build_frame_submission_payload,
)
from app.services.studio.image_task_references import resolve_reference_image_refs_by_file_ids
from app.services.studio.image_task_runner import create_image_task_and_link


async def _render_guidance(
    db: AsyncSession,
    *,
    shot_id: str,
    frame_type: ShotFrameType,
) -> dict[str, str]:
    """读取最终图片提示词需要保留的镜头约束；上下文不完整时使用空约束。"""

    try:
        run_args = await build_prompt_run_args(
            db,
            shot_id=shot_id,
            frame_type=frame_type.value,
        )
    except HTTPException:
        run_args = {}
    input_data = dict(run_args.get("input") or {})
    return {
        key: str(input_data.get(key) or "").strip()
        for key in (
            "director_command_summary",
            "continuity_guidance",
            "frame_specific_guidance",
            "composition_anchor",
            "screen_direction_guidance",
        )
    }


async def create_shot_frame_image_task(
    db: AsyncSession,
    *,
    shot_id: str,
    frame_type: ShotFrameType | str,
    prompt: str,
    linked_assets: list[ShotLinkedAssetItem] | None = None,
    model_id: str | None = None,
    target_ratio: ImageTargetRatio | str = "16:9",
    resolution_profile: ImageResolutionProfile | str | None = "standard",
) -> AsyncTaskCreateResult:
    """渲染提示词并原子暂存帧图片任务、帧槽、关联和 outbox。"""

    normalized = ShotFrameType(frame_type)
    detail = await db.get(ShotDetail, shot_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ShotDetail not found")
    normalized_prompt = prompt.strip()
    if not normalized_prompt:
        raise HTTPException(status_code=400, detail="prompt is required for shot frame generation")

    guidance = await _render_guidance(db, shot_id=shot_id, frame_type=normalized)
    submission = build_frame_submission_payload(
        base=build_frame_base_draft(
            shot_id=shot_id,
            frame_type=normalized,
            prompt=normalized_prompt,
            **guidance,
        ),
        context=build_frame_context(
            shot_id=shot_id,
            frame_type=normalized,
            items=linked_assets or [],
        ),
    )
    references = await resolve_reference_image_refs_by_file_ids(
        db,
        file_ids=submission.images,
    )
    frame = (
        await db.execute(
            select(ShotFrameImage)
            .where(
                ShotFrameImage.shot_detail_id == shot_id,
                ShotFrameImage.frame_type == normalized,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if frame is None:
        frame = ShotFrameImage(
            shot_detail_id=shot_id,
            frame_type=normalized,
            file_id=None,
            width=None,
            height=None,
            format="png",
        )
        db.add(frame)
        await db.flush()
    elif not frame.format:
        frame.format = "png"

    extra = dict(submission.extra or {})
    task_id = await create_image_task_and_link(
        db=db,
        model_id=model_id,
        relation_type="shot_frame_image",
        relation_entity_id=str(frame.id),
        prompt=submission.prompt,
        images=references or None,
        target_ratio=str(getattr(target_ratio, "value", target_ratio)),
        resolution_profile=(
            str(getattr(resolution_profile, "value", resolution_profile))
            if resolution_profile is not None
            else None
        ),
        purpose="video_reference",
        render_context=extra.get("render_context"),
        dispatch_after_commit=False,
    )
    task = await db.get(GenerationTask, task_id)
    if task is None:  # pragma: no cover - TaskManager 创建后应始终可读取
        raise RuntimeError("Image task creation did not persist a task")
    return AsyncTaskCreateResult(
        task_id=task_id,
        status=task.status,
        reused=False,
        relation_type="shot_frame_image",
        relation_entity_id=str(frame.id),
    )


__all__ = ["create_shot_frame_image_task"]
