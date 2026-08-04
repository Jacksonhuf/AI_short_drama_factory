"""固定 production manifest v1 展开服务。"""

from __future__ import annotations

from copy import deepcopy
from enum import Enum
from typing import Any

from app.models.production_runs import ProductionExecutionMode, ProductionStage

MANIFEST_VERSION = "v1"


class ProductionPreset(str, Enum):
    """可执行的固定工作流预设；不接受任意步骤图。"""

    script_assist = "script_assist"
    prepare_shots = "prepare_shots"
    prepare_frames = "prepare_frames"
    controlled_video = "controlled_video"


def _step(stage: ProductionStage, mode: ProductionExecutionMode) -> dict[str, str]:
    """构造稳定、可 JSON 序列化的 manifest 步骤。"""

    return {"key": stage.value, "mode": mode.value, "adapter_version": "v1"}


_PRESET_STEPS: dict[ProductionPreset, tuple[dict[str, str], ...]] = {
    ProductionPreset.script_assist: (
        _step(ProductionStage.script_write, ProductionExecutionMode.linear),
        _step(ProductionStage.script_review_gate, ProductionExecutionMode.gate),
    ),
    ProductionPreset.prepare_shots: (
        _step(ProductionStage.script_divide, ProductionExecutionMode.linear),
        _step(ProductionStage.script_extract, ProductionExecutionMode.linear),
        _step(ProductionStage.human_preparation_gate, ProductionExecutionMode.gate),
    ),
    ProductionPreset.prepare_frames: (
        _step(ProductionStage.script_divide, ProductionExecutionMode.linear),
        _step(ProductionStage.script_extract, ProductionExecutionMode.linear),
        _step(ProductionStage.human_preparation_gate, ProductionExecutionMode.gate),
        _step(ProductionStage.frame_prompt, ProductionExecutionMode.fan_out),
        _step(ProductionStage.frame_image, ProductionExecutionMode.fan_out),
        _step(ProductionStage.video_readiness, ProductionExecutionMode.barrier),
    ),
    ProductionPreset.controlled_video: (
        _step(ProductionStage.script_divide, ProductionExecutionMode.linear),
        _step(ProductionStage.script_extract, ProductionExecutionMode.linear),
        _step(ProductionStage.human_preparation_gate, ProductionExecutionMode.gate),
        _step(ProductionStage.frame_prompt, ProductionExecutionMode.fan_out),
        _step(ProductionStage.frame_image, ProductionExecutionMode.fan_out),
        _step(ProductionStage.video_readiness, ProductionExecutionMode.barrier),
        _step(ProductionStage.video_submit_gate, ProductionExecutionMode.gate),
        _step(ProductionStage.video_generation, ProductionExecutionMode.fan_out),
    ),
}

_SCRIPT_PROCESSING_PRESETS = {
    ProductionPreset.prepare_shots,
    ProductionPreset.prepare_frames,
    ProductionPreset.controlled_video,
}
_SCRIPT_PROCESSING_STEPS = (
    ("simplify_script", ProductionStage.script_simplify),
    ("check_consistency", ProductionStage.script_consistency),
    ("optimize_script", ProductionStage.script_optimize),
)
_FRAME_TYPES = {"first", "last", "key"}
_REFERENCE_MODES = {"first", "last", "key", "first_last", "first_last_key", "text_only"}
_VIDEO_RATIOS = {"16:9", "4:3", "1:1", "3:4", "9:16", "21:9"}


def normalize_config(preset: ProductionPreset | str, config: dict[str, Any]) -> dict[str, Any]:
    """补齐脚本处理与受控生成默认配置，并拒绝影响 manifest 的非法值。"""

    preset_value = ProductionPreset(preset)
    normalized = deepcopy(config)
    if preset_value in _SCRIPT_PROCESSING_PRESETS:
        for key, _stage in _SCRIPT_PROCESSING_STEPS:
            value = normalized.get(key, False)
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be a boolean")
            normalized[key] = value
        if normalized["optimize_script"]:
            # 优化任务依赖一致性结果，因此配置快照也显式记录隐含步骤。
            normalized["check_consistency"] = True
    if preset_value not in {
        ProductionPreset.prepare_frames,
        ProductionPreset.controlled_video,
    }:
        return normalized
    frame_types = normalized.get("frame_types", ["first"])
    if (
        not isinstance(frame_types, list)
        or not frame_types
        or any(item not in _FRAME_TYPES for item in frame_types)
    ):
        raise ValueError("frame_types must be a non-empty subset of first, last, key")
    normalized["frame_types"] = list(dict.fromkeys(frame_types))
    reference_mode = normalized.get("reference_mode", "first")
    if reference_mode not in _REFERENCE_MODES:
        raise ValueError("reference_mode is invalid")
    normalized["reference_mode"] = reference_mode
    ratio = normalized.get("video_ratio", "16:9")
    if ratio not in _VIDEO_RATIOS:
        raise ValueError("video_ratio is invalid")
    normalized["video_ratio"] = ratio
    return normalized


def expand_manifest(
    preset: ProductionPreset | str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """按归一化配置展开固定 v1 步骤，返回可独立持久化的有序快照。"""

    preset_value = ProductionPreset(preset)
    steps = list(_PRESET_STEPS[preset_value])
    if preset_value in _SCRIPT_PROCESSING_PRESETS:
        normalized = normalize_config(preset_value, config or {})
        optional_steps = [
            _step(stage, ProductionExecutionMode.linear)
            for key, stage in _SCRIPT_PROCESSING_STEPS
            if normalized[key]
        ]
        steps = [*optional_steps, *steps]
    return {
        "version": MANIFEST_VERSION,
        "preset": preset_value.value,
        "steps": deepcopy(steps),
    }


class ManifestService:
    """为调用方提供固定 manifest v1 的显式服务边界。"""

    version = MANIFEST_VERSION

    @staticmethod
    def expand(
        preset: ProductionPreset | str,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """按配置展开 preset；返回值始终是可独立持久化的深拷贝。"""

        return expand_manifest(preset, config)
