"""WP4 线性阶段适配器及注册表。

适配器只把生产步骤翻译为现有任务 service 或 gate 检查，不直接调用
Provider，也不写 run/step 状态。
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.contracts.script_writing import ScriptWriteRequest
from app.models.production_runs import (
    ChapterProductionRun,
    ChapterProductionRunStep,
    ChapterProductionRunStepItem,
    ProductionRunTaskBinding,
)
from app.models.script_task_application import ScriptTaskApplication
from app.models.studio import Chapter, Project, Shot, ShotDetail, ShotFrameType
from app.models.task import GenerationTask
from app.services.script_processing_tasks import (
    AsyncTaskCreateResult,
    create_consistency_task,
    create_divide_task,
    create_extract_task,
    create_script_optimization_task,
    create_script_simplification_task,
)
from app.services.task_dispatch import stage_task_dispatch
from app.services.script_writing import create_script_write_task
from app.services.film.generation_task_creation import (
    create_frame_prompt_task,
    create_video_task,
)
from app.services.studio.shot_frame_image_tasks import create_shot_frame_image_task
from app.services.studio.shot_preparation_state import build_shot_preparation_state
from app.services.studio.shot_video_readiness import get_shot_video_readiness


@dataclass(frozen=True, slots=True)
class GateResult:
    """人工闸门的后端校验结果与可审计快照。"""

    passed: bool
    snapshot_hash: str
    target_snapshot: dict[str, object]
    blocked_reasons: list[dict[str, object]]


@dataclass(frozen=True, slots=True)
class StageTarget:
    """fan-out/barrier 展开使用的稳定业务目标。"""

    entity_type: str
    entity_id: str
    target_key: str
    entity_version: str | None = None


class StageAdapter(Protocol):
    """线性阶段统一接口；task 与 gate adapter 分别实现所需方法。"""

    stage_key: str
    adapter_version: str
    is_gate: bool

    async def create_task(
        self,
        db: AsyncSession,
        *,
        run: ChapterProductionRun,
        step: ChapterProductionRunStep,
    ) -> AsyncTaskCreateResult:
        """通过已有业务 service 创建一个任务。"""

    async def evaluate_gate(
        self,
        db: AsyncSession,
        *,
        run: ChapterProductionRun,
        step: ChapterProductionRunStep,
    ) -> GateResult:
        """根据当前业务真相校验人工闸门。"""


class _TaskAdapter:
    """任务型 adapter 的公共属性与非法 gate 调用保护。"""

    adapter_version = "v1"
    is_gate = False

    async def evaluate_gate(
        self,
        db: AsyncSession,
        *,
        run: ChapterProductionRun,
        step: ChapterProductionRunStep,
    ) -> GateResult:
        """任务阶段不能作为人工闸门确认。"""

        del db, run, step
        raise ValueError("TASK_STAGE_IS_NOT_GATE")


class _GateAdapter:
    """闸门型 adapter 的公共属性与非法任务创建保护。"""

    adapter_version = "v1"
    is_gate = True

    async def create_task(
        self,
        db: AsyncSession,
        *,
        run: ChapterProductionRun,
        step: ChapterProductionRunStep,
    ) -> AsyncTaskCreateResult:
        """闸门阶段不能创建 GenerationTask。"""

        del db, run, step
        raise ValueError("GATE_STAGE_HAS_NO_TASK")


class _FanOutAdapter(_TaskAdapter):
    """按冻结目标创建子任务的 adapter 基类。"""

    async def targets(
        self,
        db: AsyncSession,
        *,
        run: ChapterProductionRun,
    ) -> list[StageTarget]:
        """返回当前阶段目标；编排器负责首次冻结。"""

        raise NotImplementedError

    async def create_item_task(
        self,
        db: AsyncSession,
        *,
        run: ChapterProductionRun,
        step: ChapterProductionRunStep,
        item: ChapterProductionRunStepItem,
    ) -> AsyncTaskCreateResult:
        """为一个冻结目标创建现有 GenerationTask。"""

        raise NotImplementedError


class _BarrierAdapter(_TaskAdapter):
    """同步评估冻结目标的 barrier adapter 基类。"""

    async def targets(
        self,
        db: AsyncSession,
        *,
        run: ChapterProductionRun,
    ) -> list[StageTarget]:
        """返回 barrier 目标。"""

        raise NotImplementedError

    async def evaluate_item(
        self,
        db: AsyncSession,
        *,
        run: ChapterProductionRun,
        item: ChapterProductionRunStepItem,
    ) -> GateResult:
        """实时评估一个 barrier item。"""

        raise NotImplementedError


def _stable_hash(snapshot: dict[str, object]) -> str:
    """为 gate 当前业务真相生成稳定摘要。"""

    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


class ScriptWriteStageAdapter(_TaskAdapter):
    """把 script_assist 配置转换为现有 script_write service 请求。"""

    stage_key = "script_write"

    async def create_task(
        self,
        db: AsyncSession,
        *,
        run: ChapterProductionRun,
        step: ChapterProductionRunStep,
    ) -> AsyncTaskCreateResult:
        """创建写作候选任务；缺省配置使用章节文本作为写作 premise。"""

        del step
        chapter = await db.get(Chapter, run.chapter_id)
        project = await db.get(Project, run.project_id)
        if chapter is None or project is None:
            raise ValueError("PRODUCTION_TARGET_NOT_FOUND")
        configured = dict(run.config_snapshot.get("script_write") or {})
        configured.setdefault("mode", "from_scratch")
        configured.setdefault("project_id", run.project_id)
        configured.setdefault("chapter_id", run.chapter_id)
        configured.setdefault(
            "premise",
            (chapter.raw_text or chapter.summary or chapter.title or project.description or project.name),
        )
        request = ScriptWriteRequest.model_validate(configured)
        return await create_script_write_task(db, request=request)


async def _latest_stage_task(
    db: AsyncSession,
    *,
    run_id: str,
    stage_key: str,
) -> GenerationTask | None:
    """读取本次运行指定阶段最新成功任务，供后续阶段传递持久化结果。"""

    return (
        await db.execute(
            select(GenerationTask)
            .join(
                ProductionRunTaskBinding,
                ProductionRunTaskBinding.task_id == GenerationTask.id,
            )
            .join(
                ChapterProductionRunStep,
                ChapterProductionRunStep.id == ProductionRunTaskBinding.step_id,
            )
            .where(
                ProductionRunTaskBinding.run_id == run_id,
                ChapterProductionRunStep.stage_key == stage_key,
                GenerationTask.status == "succeeded",
            )
            .order_by(ProductionRunTaskBinding.attempt.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _chapter_script_text(
    db: AsyncSession,
    *,
    run: ChapterProductionRun,
    preferred_stages: tuple[tuple[str, str], ...],
) -> str:
    """按阶段优先级读取脚本结果，缺失时回退到章节当前文本。"""

    for stage_key, result_key in preferred_stages:
        task = await _latest_stage_task(db, run_id=run.id, stage_key=stage_key)
        value = (task.result or {}).get(result_key) if task is not None else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    chapter = await db.get(Chapter, run.chapter_id)
    if chapter is None:
        raise ValueError("CHAPTER_NOT_FOUND")
    script_text = (chapter.raw_text or chapter.condensed_text or "").strip()
    if not script_text:
        raise ValueError("SCRIPT_TEXT_REQUIRED")
    return script_text


async def _stage_workflow_task(
    db: AsyncSession,
    *,
    run: ChapterProductionRun,
    task_info: AsyncTaskCreateResult,
) -> AsyncTaskCreateResult:
    """为新任务补写 outbox，并拒绝把其他入口的活动任务绑定到当前 run。"""

    if task_info.reused:
        owning_binding = (
            await db.execute(
                select(ProductionRunTaskBinding)
                .where(ProductionRunTaskBinding.task_id == task_info.task_id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if owning_binding is None or owning_binding.run_id != run.id:
            raise ValueError("ACTIVE_TASK_CONFLICT")
        return task_info
    await stage_task_dispatch(db, task_info.task_id)
    return task_info


class ScriptSimplifyStageAdapter(_TaskAdapter):
    """复用剧本精简任务，并让结果只在本次运行的后续节点中自动消费。"""

    stage_key = "script_simplify"

    async def create_task(
        self,
        db: AsyncSession,
        *,
        run: ChapterProductionRun,
        step: ChapterProductionRunStep,
    ) -> AsyncTaskCreateResult:
        """以章节原文创建精简任务，不直接覆盖章节内容。"""

        del step
        script_text = await _chapter_script_text(db, run=run, preferred_stages=())
        task_info = await create_script_simplification_task(
            db,
            relation_entity_id=run.chapter_id,
            script_text=script_text,
        )
        return await _stage_workflow_task(db, run=run, task_info=task_info)


class ScriptConsistencyStageAdapter(_TaskAdapter):
    """复用一致性检查任务，优先检查本次运行最新的精简文本。"""

    stage_key = "script_consistency"

    async def create_task(
        self,
        db: AsyncSession,
        *,
        run: ChapterProductionRun,
        step: ChapterProductionRunStep,
    ) -> AsyncTaskCreateResult:
        """以精简结果或章节原文创建一致性任务。"""

        del step
        script_text = await _chapter_script_text(
            db,
            run=run,
            preferred_stages=(("script_simplify", "simplified_script_text"),),
        )
        task_info = await create_consistency_task(
            db,
            relation_entity_id=run.chapter_id,
            script_text=script_text,
        )
        return await _stage_workflow_task(db, run=run, task_info=task_info)


class ScriptOptimizeStageAdapter(_TaskAdapter):
    """复用优化任务，组合当前脚本文本与同一次运行的一致性结果。"""

    stage_key = "script_optimize"

    async def create_task(
        self,
        db: AsyncSession,
        *,
        run: ChapterProductionRun,
        step: ChapterProductionRunStep,
    ) -> AsyncTaskCreateResult:
        """读取精简文本和一致性任务结果后创建优化任务。"""

        del step
        script_text = await _chapter_script_text(
            db,
            run=run,
            preferred_stages=(("script_simplify", "simplified_script_text"),),
        )
        consistency_task = await _latest_stage_task(
            db,
            run_id=run.id,
            stage_key="script_consistency",
        )
        if consistency_task is None or not consistency_task.result:
            raise ValueError("SCRIPT_CONSISTENCY_RESULT_REQUIRED")
        task_info = await create_script_optimization_task(
            db,
            relation_entity_id=run.chapter_id,
            script_text=script_text,
            consistency=dict(consistency_task.result),
        )
        return await _stage_workflow_task(db, run=run, task_info=task_info)


class ScriptDivideStageAdapter(_TaskAdapter):
    """复用 divide service 将章节正文拆分并写入镜头。"""

    stage_key = "script_divide"

    async def create_task(
        self,
        db: AsyncSession,
        *,
        run: ChapterProductionRun,
        step: ChapterProductionRunStep,
    ) -> AsyncTaskCreateResult:
        """以当前章节正文创建自动落库的拆分任务。"""

        del step
        script_text = await _chapter_script_text(
            db,
            run=run,
            preferred_stages=(
                ("script_optimize", "optimized_script_text"),
                ("script_simplify", "simplified_script_text"),
            ),
        )
        return await create_divide_task(
            db,
            chapter_id=run.chapter_id,
            script_text=script_text,
            write_to_db=True,
        )


class ScriptExtractStageAdapter(_TaskAdapter):
    """复用 extract service，输入取自本次 run 的成功 divide 任务。"""

    stage_key = "script_extract"

    async def create_task(
        self,
        db: AsyncSession,
        *,
        run: ChapterProductionRun,
        step: ChapterProductionRunStep,
    ) -> AsyncTaskCreateResult:
        """读取前一步持久化结果并创建提取任务。"""

        del step
        divide_task = await _latest_stage_task(
            db,
            run_id=run.id,
            stage_key="script_divide",
        )
        if divide_task is None or not divide_task.result:
            raise ValueError("SCRIPT_DIVISION_RESULT_REQUIRED")
        consistency_task = await _latest_stage_task(
            db,
            run_id=run.id,
            stage_key="script_consistency",
        )
        return await create_extract_task(
            db,
            project_id=run.project_id,
            chapter_id=run.chapter_id,
            script_division=divide_task.result,
            consistency=(
                dict(consistency_task.result)
                if consistency_task is not None and consistency_task.result
                else None
            ),
            refresh_cache=bool(run.config_snapshot.get("refresh_extraction_cache", False)),
        )


class ScriptReviewGateAdapter(_GateAdapter):
    """仅当本次 run 的 script_write 候选已显式应用时放行。"""

    stage_key = "script_review_gate"

    async def evaluate_gate(
        self,
        db: AsyncSession,
        *,
        run: ChapterProductionRun,
        step: ChapterProductionRunStep,
    ) -> GateResult:
        """查询 binding 对应的 ScriptTaskApplication，禁止确认其他任务的结果。"""

        del step
        application = (
            await db.execute(
                select(ScriptTaskApplication)
                .join(
                    ProductionRunTaskBinding,
                    ProductionRunTaskBinding.task_id == ScriptTaskApplication.task_id,
                )
                .where(
                    ProductionRunTaskBinding.run_id == run.id,
                    ScriptTaskApplication.chapter_id == run.chapter_id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        snapshot: dict[str, object] = {
            "chapter_id": run.chapter_id,
            "application_id": application.id if application is not None else None,
            "task_id": application.task_id if application is not None else None,
        }
        reasons = [] if application is not None else [{
            "code": "SCRIPT_RESULT_NOT_APPLIED",
            "message": "写作候选尚未显式应用到章节",
        }]
        return GateResult(application is not None, _stable_hash(snapshot), snapshot, reasons)


class HumanPreparationGateAdapter(_GateAdapter):
    """聚合目标章节全部镜头的现有 preparation-state。"""

    stage_key = "human_preparation_gate"

    async def evaluate_gate(
        self,
        db: AsyncSession,
        *,
        run: ChapterProductionRun,
        step: ChapterProductionRunStep,
    ) -> GateResult:
        """逐镜头调用统一准备态服务，避免复制 ready 判定。"""

        del step
        shots = (
            await db.execute(
                select(Shot)
                .where(Shot.chapter_id == run.chapter_id)
                .order_by(Shot.index, Shot.id)
            )
        ).scalars().all()
        states = [
            await build_shot_preparation_state(db, shot_id=shot.id)
            for shot in shots
        ]
        blocked = [
            {
                "code": "SHOT_PREPARATION_INCOMPLETE",
                "message": "镜头尚未完成生成前准备",
                "entity_ref": {"type": "shot", "id": shot.id},
            }
            for shot, state in zip(shots, states, strict=True)
            if not state.ready_for_generation
        ]
        if not shots:
            blocked.append({
                "code": "NO_TARGET_SHOTS",
                "message": "章节尚无可确认的目标镜头",
            })
        snapshot = {
            "chapter_id": run.chapter_id,
            "shots": [
                {"id": shot.id, "ready_for_generation": state.ready_for_generation}
                for shot, state in zip(shots, states, strict=True)
            ],
        }
        return GateResult(not blocked, _stable_hash(snapshot), snapshot, blocked)


async def _chapter_shot_targets(
    db: AsyncSession,
    *,
    run: ChapterProductionRun,
) -> list[StageTarget]:
    """读取章节当前镜头，首次 fan-out 后由编排器冻结 ID 集合。"""

    shots = (
        await db.execute(
            select(Shot)
            .where(Shot.chapter_id == run.chapter_id)
            .order_by(Shot.index, Shot.id)
        )
    ).scalars().all()
    return [
        StageTarget(
            entity_type="shot",
            entity_id=shot.id,
            target_key=shot.id,
            entity_version=(
                shot.updated_at.isoformat() if shot.updated_at is not None else None
            ),
        )
        for shot in shots
    ]


class FramePromptStageAdapter(_FanOutAdapter):
    """为冻结镜头和配置帧类型创建现有帧提示词任务。"""

    stage_key = "frame_prompt"

    async def targets(
        self,
        db: AsyncSession,
        *,
        run: ChapterProductionRun,
    ) -> list[StageTarget]:
        """按 shot × frame_type 展开稳定目标键。"""

        shots = await _chapter_shot_targets(db, run=run)
        return [
            StageTarget(
                entity_type="shot",
                entity_id=shot.entity_id,
                target_key=f"{shot.entity_id}:{frame_type}",
                entity_version=shot.entity_version,
            )
            for shot in shots
            for frame_type in run.config_snapshot["frame_types"]
        ]

    async def create_item_task(
        self,
        db: AsyncSession,
        *,
        run: ChapterProductionRun,
        step: ChapterProductionRunStep,
        item: ChapterProductionRunStepItem,
    ) -> AsyncTaskCreateResult:
        """调用帧提示词创建 service。"""

        del run, step
        return await create_frame_prompt_task(
            db,
            shot_id=item.entity_id,
            frame_type=item.target_key.rsplit(":", 1)[1],
        )


class FrameImageStageAdapter(_FanOutAdapter):
    """为帧提示词阶段冻结的同一目标创建现有图片任务。"""

    stage_key = "frame_image"

    async def targets(
        self,
        db: AsyncSession,
        *,
        run: ChapterProductionRun,
    ) -> list[StageTarget]:
        """按 shot × frame_type 展开稳定目标键。"""

        return await FramePromptStageAdapter().targets(db, run=run)

    async def create_item_task(
        self,
        db: AsyncSession,
        *,
        run: ChapterProductionRun,
        step: ChapterProductionRunStep,
        item: ChapterProductionRunStepItem,
    ) -> AsyncTaskCreateResult:
        """读取已生成帧提示词并调用帧图片 service。"""

        del step
        detail = await db.get(ShotDetail, item.entity_id)
        if detail is None:
            raise ValueError("SHOT_DETAIL_NOT_FOUND")
        frame_type = ShotFrameType(item.target_key.rsplit(":", 1)[1])
        prompt = {
            ShotFrameType.first: detail.first_frame_prompt,
            ShotFrameType.last: detail.last_frame_prompt,
            ShotFrameType.key: detail.key_frame_prompt,
        }[frame_type]
        return await create_shot_frame_image_task(
            db,
            shot_id=item.entity_id,
            frame_type=frame_type,
            prompt=prompt,
            target_ratio=run.config_snapshot["video_ratio"],
        )


class VideoReadinessStageAdapter(_BarrierAdapter):
    """逐冻结镜头复用统一 video-readiness 服务。"""

    stage_key = "video_readiness"

    async def targets(
        self,
        db: AsyncSession,
        *,
        run: ChapterProductionRun,
    ) -> list[StageTarget]:
        """按镜头展开 readiness 目标。"""

        return await _chapter_shot_targets(db, run=run)

    async def evaluate_item(
        self,
        db: AsyncSession,
        *,
        run: ChapterProductionRun,
        item: ChapterProductionRunStepItem,
    ) -> GateResult:
        """实时聚合单镜头准备度并转为可审计结果。"""

        readiness = await get_shot_video_readiness(
            db,
            shot_id=item.entity_id,
            reference_mode=run.config_snapshot["reference_mode"],
        )
        snapshot = readiness.model_dump(mode="json")
        blocked = [
            {
                "code": str(check.key).upper(),
                "message": check.message,
                "entity_ref": {"type": "shot", "id": item.entity_id},
            }
            for check in readiness.checks
            if not check.ok
        ]
        return GateResult(
            readiness.ready,
            _stable_hash(snapshot),
            snapshot,
            blocked,
        )


class VideoSubmitGateAdapter(_GateAdapter):
    """显式确认 readiness 已通过的冻结镜头视频提交。"""

    stage_key = "video_submit_gate"

    async def evaluate_gate(
        self,
        db: AsyncSession,
        *,
        run: ChapterProductionRun,
        step: ChapterProductionRunStep,
    ) -> GateResult:
        """确认前重新校验每个仍存在的冻结镜头，防止 barrier 后实体漂移。"""

        del step
        shot_ids = list(run.input_snapshot.get("target_shot_ids") or [])
        existing_ids = set(
            (await db.execute(select(Shot.id).where(Shot.id.in_(shot_ids)))).scalars()
        ) if shot_ids else set()
        target_ids = [shot_id for shot_id in shot_ids if shot_id in existing_ids]
        readiness_rows = [
            await get_shot_video_readiness(
                db,
                shot_id=shot_id,
                reference_mode=run.config_snapshot["reference_mode"],
            )
            for shot_id in target_ids
        ]
        blocked = [
            {
                "code": str(check.key).upper(),
                "message": check.message,
                "entity_ref": {"type": "shot", "id": readiness.shot_id},
            }
            for readiness in readiness_rows
            for check in readiness.checks
            if not check.ok
        ]
        if not target_ids:
            blocked.append({
                "code": "NO_TARGET_SHOTS",
                "message": "没有仍存在的冻结镜头可提交视频生成",
            })
        snapshot = {
            "chapter_id": run.chapter_id,
            "target_shot_ids": shot_ids,
            "readiness": [
                readiness.model_dump(mode="json") for readiness in readiness_rows
            ],
            "reference_mode": run.config_snapshot["reference_mode"],
            "video_ratio": run.config_snapshot["video_ratio"],
        }
        return GateResult(not blocked, _stable_hash(snapshot), snapshot, blocked)


class VideoGenerationStageAdapter(_FanOutAdapter):
    """确认后为冻结镜头创建现有视频任务并复用结果回流。"""

    stage_key = "video_generation"

    async def targets(
        self,
        db: AsyncSession,
        *,
        run: ChapterProductionRun,
    ) -> list[StageTarget]:
        """按镜头展开视频目标。"""

        return await _chapter_shot_targets(db, run=run)

    async def create_item_task(
        self,
        db: AsyncSession,
        *,
        run: ChapterProductionRun,
        step: ChapterProductionRunStep,
        item: ChapterProductionRunStepItem,
    ) -> AsyncTaskCreateResult:
        """调用视频创建 service；executor 继续负责文件和 shot 回流。"""

        del step
        return await create_video_task(
            db,
            shot_id=item.entity_id,
            reference_mode=run.config_snapshot["reference_mode"],
            ratio=run.config_snapshot["video_ratio"],
        )


class StageAdapterRegistry:
    """按稳定 stage key/version 解析 adapter，启动时即拒绝遗漏阶段。"""

    def __init__(self) -> None:
        self._adapters: dict[tuple[str, str], StageAdapter] = {}

    def register(self, adapter: StageAdapter) -> None:
        """注册 adapter；重复键表示启动配置错误。"""

        key = (adapter.stage_key, adapter.adapter_version)
        if key in self._adapters:
            raise ValueError(f"duplicate stage adapter: {key}")
        self._adapters[key] = adapter

    def resolve(self, stage_key: str, adapter_version: str = "v1") -> StageAdapter:
        """解析持久化步骤所指定的 adapter。"""

        try:
            return self._adapters[(stage_key, adapter_version)]
        except KeyError as exc:
            raise ValueError(f"stage adapter not registered: {stage_key}@{adapter_version}") from exc


stage_adapter_registry = StageAdapterRegistry()
for _adapter in (
    ScriptWriteStageAdapter(),
    ScriptReviewGateAdapter(),
    ScriptSimplifyStageAdapter(),
    ScriptConsistencyStageAdapter(),
    ScriptOptimizeStageAdapter(),
    ScriptDivideStageAdapter(),
    ScriptExtractStageAdapter(),
    HumanPreparationGateAdapter(),
    FramePromptStageAdapter(),
    FrameImageStageAdapter(),
    VideoReadinessStageAdapter(),
    VideoSubmitGateAdapter(),
    VideoGenerationStageAdapter(),
):
    stage_adapter_registry.register(_adapter)


__all__ = [
    "GateResult",
    "StageAdapter",
    "StageAdapterRegistry",
    "stage_adapter_registry",
]
