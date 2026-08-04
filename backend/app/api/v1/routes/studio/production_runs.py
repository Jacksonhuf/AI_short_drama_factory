"""Studio 章节生产运行的生命周期与 WP4 gate API。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.utils import paginate
from app.dependencies import get_db
from app.models.production_runs import (
    ChapterProductionRun,
    ChapterProductionRunStep,
    ChapterProductionRunStepItem,
)
from app.schemas.common import (
    ApiResponse,
    PaginatedData,
    created_response,
    paginated_response,
    success_response,
)
from app.schemas.studio.production_runs import (
    ProductionRunCreateRequest,
    ProductionRunConfirmRequest,
    ProductionRunItemMutationRequest,
    ProductionRunMutationRequest,
    ProductionRunRead,
    ProductionRunStepRead,
    ProductionRunStepItemRead,
    ProductionRunSummaryRead,
)
from app.services.production_runs.transitions import (
    ProductionRunTransitionService,
    TransitionResult,
)
from app.services.production_runs.dispatch import dispatch_due_run_outboxes

chapter_runs_router = APIRouter()
router = APIRouter()


async def _steps_for_run(
    db: AsyncSession, run_id: str
) -> list[ProductionRunStepRead]:
    """读取有序步骤，避免在异步响应序列化期间触发 lazy load。"""

    steps = (
        await db.execute(
            select(ChapterProductionRunStep)
            .where(ChapterProductionRunStep.run_id == run_id)
            .order_by(ChapterProductionRunStep.sequence)
        )
    ).scalars()
    return [ProductionRunStepRead.model_validate(step) for step in steps]


async def _detail_from_run(
    db: AsyncSession, run: ChapterProductionRun
) -> ProductionRunRead:
    """将当前 ORM 状态组装为不含敏感任务上下文的详情 DTO。"""

    summary = ProductionRunSummaryRead.model_validate(run)
    return ProductionRunRead(
        **summary.model_dump(),
        steps=await _steps_for_run(db, run.id),
    )


async def _detail_from_transition(
    db: AsyncSession, result: TransitionResult
) -> ProductionRunRead:
    """使用首次 mutation 快照响应幂等重放，并补充固定步骤列表。"""

    summary = ProductionRunSummaryRead.model_validate(result.result_snapshot)
    return ProductionRunRead(
        **summary.model_dump(),
        steps=await _steps_for_run(db, result.run.id),
    )


@chapter_runs_router.post(
    "/{chapter_id}/production-runs",
    response_model=ApiResponse[ProductionRunRead],
    status_code=status.HTTP_201_CREATED,
    summary="创建章节生产运行",
)
async def create_production_run(
    chapter_id: str,
    body: ProductionRunCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ProductionRunRead]:
    """创建 draft 运行并持久化 manifest v1 快照和步骤。"""

    result = await ProductionRunTransitionService(db).draft(
        chapter_id=chapter_id,
        preset=body.preset,
        config=body.config,
        idempotency_key=body.idempotency_key,
    )
    return created_response(await _detail_from_transition(db, result))


@chapter_runs_router.get(
    "/{chapter_id}/production-runs",
    response_model=ApiResponse[PaginatedData[ProductionRunSummaryRead]],
    summary="列出章节生产运行",
)
async def list_production_runs(
    chapter_id: str,
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> ApiResponse[PaginatedData[ProductionRunSummaryRead]]:
    """按创建时间倒序列出指定章节的历史运行。"""

    statement = (
        select(ChapterProductionRun)
        .where(ChapterProductionRun.chapter_id == chapter_id)
        .order_by(ChapterProductionRun.created_at.desc())
    )
    runs, total = await paginate(db, stmt=statement, page=page, page_size=page_size)
    return paginated_response(
        [ProductionRunSummaryRead.model_validate(run) for run in runs],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/{run_id}",
    response_model=ApiResponse[ProductionRunRead],
    summary="获取章节生产运行详情",
)
async def get_production_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ProductionRunRead]:
    """返回运行标量和步骤汇总，不展开 item、prompt 或 Provider 响应。"""

    run = await db.get(ChapterProductionRun, run_id)
    if run is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Production run not found")
    return success_response(await _detail_from_run(db, run))


async def _mutate(
    db: AsyncSession,
    run_id: str,
    body: ProductionRunMutationRequest,
    mutation: Callable[..., Awaitable[TransitionResult]],
    *,
    dispatch_after_commit: bool = False,
) -> ApiResponse[ProductionRunRead]:
    """统一 mutation；需要派发时严格先提交 outbox 再 publish。"""

    result = await mutation(
        run_id,
        expected_lock_version=body.expected_lock_version,
        idempotency_key=body.idempotency_key,
    )
    response = success_response(await _detail_from_transition(db, result))
    await db.commit()
    if dispatch_after_commit:
        await dispatch_due_run_outboxes(db, run_id=run_id)
    return response


@router.post(
    "/{run_id}/start",
    response_model=ApiResponse[ProductionRunRead],
    summary="启动章节生产运行",
)
async def start_production_run(
    run_id: str,
    body: ProductionRunMutationRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ProductionRunRead]:
    """将 draft 运行置为 running，并通过 outbox 自动派发首步。"""

    service = ProductionRunTransitionService(db)
    return await _mutate(
        db,
        run_id,
        body,
        service.start,
        dispatch_after_commit=True,
    )


@router.post(
    "/{run_id}/pause",
    response_model=ApiResponse[ProductionRunRead],
    summary="暂停章节生产运行",
)
async def pause_production_run(
    run_id: str,
    body: ProductionRunMutationRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ProductionRunRead]:
    """暂停 running 运行。"""

    service = ProductionRunTransitionService(db)
    return await _mutate(db, run_id, body, service.pause)


@router.post(
    "/{run_id}/resume",
    response_model=ApiResponse[ProductionRunRead],
    summary="恢复章节生产运行",
)
async def resume_production_run(
    run_id: str,
    body: ProductionRunMutationRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ProductionRunRead]:
    """仅恢复 paused 运行；人工等待状态不走此入口。"""

    service = ProductionRunTransitionService(db)
    return await _mutate(
        db,
        run_id,
        body,
        service.resume,
        dispatch_after_commit=True,
    )


@router.post(
    "/{run_id}/cancel",
    response_model=ApiResponse[ProductionRunRead],
    summary="取消章节生产运行",
)
async def cancel_production_run(
    run_id: str,
    body: ProductionRunMutationRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ProductionRunRead]:
    """取消非终态运行并释放同章节活跃槽。"""

    service = ProductionRunTransitionService(db)
    return await _mutate(db, run_id, body, service.cancel)


@router.post(
    "/{run_id}/steps/{step_id}/confirm",
    response_model=ApiResponse[ProductionRunRead],
    summary="确认章节生产运行人工闸门",
)
async def confirm_production_run_step(
    run_id: str,
    step_id: str,
    body: ProductionRunConfirmRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ProductionRunRead]:
    """执行 gate 业务校验后推进；waiting_human 不能通过 resume 绕过。"""

    from app.services.production_runs.orchestration import confirm_gate

    run, transition, replay = await confirm_gate(
        db,
        run_id=run_id,
        step_id=step_id,
        expected_lock_version=body.expected_lock_version,
        idempotency_key=body.idempotency_key,
    )
    result = TransitionResult(
        run=run,
        transition=transition,
        replay=replay,
        result_snapshot=transition.result_snapshot,
    )
    response = success_response(await _detail_from_transition(db, result))
    await db.commit()
    await dispatch_due_run_outboxes(db, run_id=run_id)
    return response


@router.get(
    "/{run_id}/steps/{step_id}/items",
    response_model=ApiResponse[PaginatedData[ProductionRunStepItemRead]],
    summary="分页列出生产步骤项",
)
async def list_production_run_step_items(
    run_id: str,
    step_id: str,
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> ApiResponse[PaginatedData[ProductionRunStepItemRead]]:
    """分页返回冻结 item、结算状态和结构化阻断原因。"""

    statement = (
        select(ChapterProductionRunStepItem)
        .join(
            ChapterProductionRunStep,
            ChapterProductionRunStep.id == ChapterProductionRunStepItem.step_id,
        )
        .where(
            ChapterProductionRunStep.run_id == run_id,
            ChapterProductionRunStepItem.step_id == step_id,
        )
        .order_by(ChapterProductionRunStepItem.target_key)
    )
    items, total = await paginate(db, stmt=statement, page=page, page_size=page_size)
    return paginated_response(
        [ProductionRunStepItemRead.model_validate(item) for item in items],
        page=page,
        page_size=page_size,
        total=total,
    )


async def _mutate_item(
    db: AsyncSession,
    *,
    run_id: str,
    step_id: str,
    item_id: str,
    action: str,
    body: ProductionRunItemMutationRequest,
) -> ApiResponse[ProductionRunStepItemRead]:
    """统一定向 item mutation，并在提交后投递可能创建的重试任务。"""

    from app.services.production_runs.orchestration import mutate_step_item

    _, item, _ = await mutate_step_item(
        db,
        run_id=run_id,
        step_id=step_id,
        item_id=item_id,
        action=action,
        expected_lock_version=body.expected_lock_version,
        idempotency_key=body.idempotency_key,
    )
    response = success_response(ProductionRunStepItemRead.model_validate(item))
    await db.commit()
    await dispatch_due_run_outboxes(db, run_id=run_id)
    return response


@router.post(
    "/{run_id}/steps/{step_id}/items/{item_id}/retry",
    response_model=ApiResponse[ProductionRunStepItemRead],
    summary="重试生产步骤项",
)
async def retry_production_run_step_item(
    run_id: str,
    step_id: str,
    item_id: str,
    body: ProductionRunItemMutationRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ProductionRunStepItemRead]:
    """仅重试指定失败项，不重跑已成功项。"""

    return await _mutate_item(
        db,
        run_id=run_id,
        step_id=step_id,
        item_id=item_id,
        action="retry",
        body=body,
    )


@router.post(
    "/{run_id}/steps/{step_id}/items/{item_id}/skip",
    response_model=ApiResponse[ProductionRunStepItemRead],
    summary="跳过生产步骤项",
)
async def skip_production_run_step_item(
    run_id: str,
    step_id: str,
    item_id: str,
    body: ProductionRunItemMutationRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ProductionRunStepItemRead]:
    """排除指定失败项并重新结算 barrier。"""

    return await _mutate_item(
        db,
        run_id=run_id,
        step_id=step_id,
        item_id=item_id,
        action="skip",
        body=body,
    )
