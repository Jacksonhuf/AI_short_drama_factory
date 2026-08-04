"""模型任务入队前的本地配置预检。"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.contracts.image_generation import ImageGenerationInput
from app.core.contracts.video_generation import VideoGenerationInput
from app.core.integrations.image_capabilities import (
    resolve_image_size,
    validate_image_options,
)
from app.core.integrations.video_capabilities import validate_video_options
from app.models.llm import Model, ModelCategoryKey
from app.services.llm.provider_resolver import resolve_provider_config_by_model
from app.services.llm.resolver import get_default_model_by_category


_CATEGORY_LABELS = {
    ModelCategoryKey.text: "文本",
    ModelCategoryKey.image: "图片",
    ModelCategoryKey.video: "视频",
}


def _unavailable(category: ModelCategoryKey, reason: object) -> HTTPException:
    """把底层配置错误统一为可指导管理员修复的 503。"""

    label = _CATEGORY_LABELS[category]
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            f"{label}模型不可用：{reason}。"
            "请前往模型管理检查默认模型、供应商启用状态和 API Key。"
        ),
    )


async def preflight_default_model(
    db: AsyncSession,
    *,
    category: ModelCategoryKey,
    target_ratio: str | None = None,
) -> Model:
    """仅检查数据库配置和本地能力声明，不向模型供应商发起网络请求。"""

    try:
        model = await get_default_model_by_category(db, category)
        provider = await resolve_provider_config_by_model(db, model=model)
        if category == ModelCategoryKey.image:
            image_input = ImageGenerationInput(
                prompt="preflight",
                model=model.name,
                purpose="video_reference",
                target_ratio=target_ratio,
            )
            resolve_image_size(
                provider=provider.provider_key,  # type: ignore[arg-type]
                model=model.name,
                purpose=image_input.purpose,
                target_ratio=image_input.target_ratio,
                resolution_profile=image_input.resolution_profile,
                requested_size=image_input.size,
            )
            validate_image_options(
                provider=provider.provider_key,  # type: ignore[arg-type]
                model=model.name,
                input_=image_input,
            )
        elif category == ModelCategoryKey.video:
            video_input = VideoGenerationInput(
                prompt="preflight",
                model=model.name,
                ratio=target_ratio or "16:9",
            )
            validate_video_options(
                provider=provider.provider_key,  # type: ignore[arg-type]
                model=model.name,
                input_=video_input,
            )
        return model
    except HTTPException as exc:
        raise _unavailable(category, exc.detail) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise _unavailable(category, str(exc)) from exc
