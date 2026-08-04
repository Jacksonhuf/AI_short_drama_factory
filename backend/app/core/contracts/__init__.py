"""生成任务共享契约导出。"""

from app.core.contracts.image_generation import (
    ImageGenerationInput,
    ImageGenerationResult,
    ImageItem,
    InputImageRef,
    ResponseFormat,
)
from app.core.contracts.provider import ProviderConfig, ProviderKey
from app.core.contracts.video_generation import VideoGenerationInput, VideoGenerationResult
from app.core.contracts.script_writing import (
    AppliedScriptTaskResult,
    ApplyScriptTaskResultRequest,
    ScriptApplyTargetField,
    ScriptWriteMode,
    ScriptWriteRequest,
    ScriptWriteResult,
)

__all__ = [
    "ProviderConfig",
    "ProviderKey",
    "VideoGenerationInput",
    "VideoGenerationResult",
    "ImageGenerationInput",
    "ImageGenerationResult",
    "ImageItem",
    "InputImageRef",
    "ResponseFormat",
    "AppliedScriptTaskResult",
    "ApplyScriptTaskResultRequest",
    "ScriptApplyTargetField",
    "ScriptWriteMode",
    "ScriptWriteRequest",
    "ScriptWriteResult",
]
