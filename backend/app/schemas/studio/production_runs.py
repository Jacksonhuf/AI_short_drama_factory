"""章节生产运行 API 的严格请求与响应 DTO。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.production_runs import (
    ProductionExecutionMode,
    ProductionRunStatus,
    ProductionStepStatus,
)
from app.services.production_runs.manifest import ProductionPreset


class ProductionRunCreateRequest(BaseModel):
    """创建固定 preset 运行的请求。"""

    model_config = ConfigDict(extra="forbid")

    preset: ProductionPreset
    config: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=128)


class ProductionRunMutationRequest(BaseModel):
    """所有生命周期 mutation 共享的乐观锁和幂等参数。"""

    model_config = ConfigDict(extra="forbid")

    expected_lock_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=128)


class ProductionRunConfirmRequest(ProductionRunMutationRequest):
    """人工 gate 确认请求；与 resume 分离以禁止绕过业务校验。"""


class ProductionRunItemMutationRequest(ProductionRunMutationRequest):
    """fan-out item 定向 retry/skip 请求。"""


class ProductionRunStepRead(BaseModel):
    """运行详情中的轻量步骤状态。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    stage_key: str
    step_order: int
    adapter_version: str
    execution_mode: ProductionExecutionMode
    status: ProductionStepStatus
    attempt: int
    input_snapshot: dict[str, Any]
    target_snapshot: dict[str, Any]
    output_summary: dict[str, Any]
    blocked_reasons: list[dict[str, Any]]
    lock_version: int
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ProductionRunStepItemRead(BaseModel):
    """分页读取的 fan-out/barrier item，不暴露 Provider 上下文。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    step_id: str
    entity_type: str
    entity_id: str
    target_key: str
    entity_version: str | None
    status: ProductionStepStatus
    attempt: int
    blocked_reasons: list[dict[str, Any]]
    output_ref: dict[str, Any]
    lock_version: int
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ProductionRunSummaryRead(BaseModel):
    """列表和 mutation 返回的运行标量。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    chapter_id: str
    preset_key: ProductionPreset
    manifest_version: str
    manifest_snapshot: dict[str, Any]
    config_snapshot: dict[str, Any]
    input_snapshot: dict[str, Any]
    target_snapshot_hash: str | None
    status: ProductionRunStatus
    current_step_id: str | None
    lock_version: int
    transition_version: int
    cancel_requested: bool
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ProductionRunRead(ProductionRunSummaryRead):
    """运行详情，内嵌有序步骤但不展开大量 fan-out item。"""

    steps: list[ProductionRunStepRead] = Field(default_factory=list)
