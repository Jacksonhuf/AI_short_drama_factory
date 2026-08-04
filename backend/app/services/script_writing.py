"""AI 剧本写作任务创建与候选结果显式应用服务。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.contracts.script_writing import (
    AppliedScriptTaskResult,
    ApplyScriptTaskResultRequest,
    ScriptWriteRequest,
    ScriptWriteResult,
)
from app.core.task_manager import DeliveryMode, SqlAlchemyTaskStore, TaskManager
from app.models.script_task_application import ScriptTaskApplication
from app.models.studio import Chapter, Project
from app.models.task import GenerationTask, GenerationTaskStatus
from app.models.task_links import GenerationTaskLink
from app.models.llm import ModelCategoryKey
from app.services.llm.preflight import preflight_default_model
from app.services.script_processing_tasks import AsyncTaskCreateResult
from app.services.task_dispatch import stage_task_dispatch

SCRIPT_WRITE_TASK_KIND = "script_write"
SCRIPT_WRITING_RELATION_TYPE = "script_writing"


class _CreateOnlyTask:
    """仅用于 TaskManager 持久化，由 Celery registry 负责实际执行。"""

    async def run(self, *args: object, **kwargs: object) -> None:
        return None

    async def status(self) -> dict[str, object]:
        return {}

    async def is_done(self) -> bool:
        return False

    async def get_result(self) -> object:
        return None


def _timestamp_key(value: datetime) -> datetime:
    """把数据库可能返回的 naive UTC 与 API aware 时间统一后进行精确版本比较。"""

    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value


async def create_script_write_task(
    db: AsyncSession,
    *,
    request: ScriptWriteRequest,
) -> AsyncTaskCreateResult:
    """校验项目、章节和默认文本模型后原子创建任务、关联与 outbox。"""

    project = await db.get(Project, request.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="PROJECT_NOT_FOUND")
    if request.chapter_id:
        chapter = await db.get(Chapter, request.chapter_id)
        if chapter is None or chapter.project_id != request.project_id:
            raise HTTPException(status_code=404, detail="CHAPTER_NOT_FOUND")

    # 预检只读取本地模型配置，确保配置错误不会留下永远无法执行的任务或 outbox。
    await preflight_default_model(db, category=ModelCategoryKey.text)

    store = SqlAlchemyTaskStore(db)
    manager = TaskManager(store=store, strategies={})
    record = await manager.create(
        task=_CreateOnlyTask(),
        mode=DeliveryMode.async_polling,
        task_kind=SCRIPT_WRITE_TASK_KIND,
        run_args={"request": request.model_dump(mode="json")},
    )
    relation_entity_id = request.chapter_id or request.project_id
    db.add(
        GenerationTaskLink(
            task_id=record.id,
            resource_type="task_link",
            relation_type=SCRIPT_WRITING_RELATION_TYPE,
            relation_entity_id=relation_entity_id,
        )
    )
    await db.flush()
    await stage_task_dispatch(db, record.id)
    return AsyncTaskCreateResult(
        task_id=record.id,
        status=record.status,
        reused=False,
        relation_type=SCRIPT_WRITING_RELATION_TYPE,
        relation_entity_id=relation_entity_id,
    )


def _application_read(
    row: ScriptTaskApplication,
    *,
    replay: bool,
) -> AppliedScriptTaskResult:
    """把稳定应用记录转换为完整 API 响应。"""

    return AppliedScriptTaskResult(
        application_id=row.id,
        task_id=row.task_id,
        chapter_id=row.chapter_id,
        target_field=row.target_field,
        chapter_updated_at=row.chapter_updated_at,
        idempotent_replay=replay,
    )


def _same_application_request(
    row: ScriptTaskApplication,
    *,
    chapter_id: str,
    request: ApplyScriptTaskResultRequest,
) -> bool:
    """判断幂等重放是否与首次请求语义完全一致。"""

    return (
        row.task_id == request.task_id
        and row.chapter_id == chapter_id
        and row.target_field == request.target_field.value
        and _timestamp_key(row.expected_chapter_updated_at)
        == _timestamp_key(request.expected_chapter_updated_at)
    )


async def apply_script_task_result(
    db: AsyncSession,
    *,
    chapter_id: str,
    request: ApplyScriptTaskResultRequest,
) -> AppliedScriptTaskResult:
    """锁定章节并原子校验任务、结果、版本与幂等记录后应用候选正文。"""

    by_key = (
        await db.execute(
            select(ScriptTaskApplication)
            .where(ScriptTaskApplication.idempotency_key == request.idempotency_key)
            .limit(1)
        )
    ).scalar_one_or_none()
    if by_key is not None:
        if not _same_application_request(by_key, chapter_id=chapter_id, request=request):
            raise HTTPException(status_code=409, detail="IDEMPOTENCY_KEY_CONFLICT")
        return _application_read(by_key, replay=True)

    by_task = (
        await db.execute(
            select(ScriptTaskApplication)
            .where(ScriptTaskApplication.task_id == request.task_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if by_task is not None:
        if by_task.chapter_id != chapter_id or by_task.target_field != request.target_field.value:
            raise HTTPException(status_code=409, detail="TASK_RESULT_ALREADY_APPLIED")
        return _application_read(by_task, replay=True)

    chapter = (
        await db.execute(
            select(Chapter).where(Chapter.id == chapter_id).with_for_update()
        )
    ).scalar_one_or_none()
    if chapter is None:
        raise HTTPException(status_code=404, detail="CHAPTER_NOT_FOUND")

    task = (
        await db.execute(
            select(GenerationTask).where(GenerationTask.id == request.task_id).with_for_update()
        )
    ).scalar_one_or_none()
    if task is None or task.task_kind != SCRIPT_WRITE_TASK_KIND:
        raise HTTPException(status_code=404, detail="SCRIPT_WRITE_TASK_NOT_FOUND")
    if task.status != GenerationTaskStatus.succeeded:
        raise HTTPException(status_code=409, detail="TASK_NOT_SUCCEEDED")

    link = (
        await db.execute(
            select(GenerationTaskLink).where(
                GenerationTaskLink.task_id == request.task_id,
                GenerationTaskLink.relation_type == SCRIPT_WRITING_RELATION_TYPE,
                GenerationTaskLink.relation_entity_id == chapter_id,
            )
        )
    ).scalar_one_or_none()
    run_request = dict(((task.payload or {}).get("run_args") or {}).get("request") or {})
    if link is None or run_request.get("chapter_id") != chapter_id:
        raise HTTPException(status_code=404, detail="SCRIPT_WRITE_TASK_NOT_FOUND")

    try:
        result = ScriptWriteResult.model_validate(task.result)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="TASK_RESULT_INVALID") from exc

    if _timestamp_key(chapter.updated_at) != _timestamp_key(
        request.expected_chapter_updated_at
    ):
        raise HTTPException(status_code=409, detail="CHAPTER_MODIFIED")

    applied_at = datetime.now(UTC).replace(tzinfo=None)
    setattr(chapter, request.target_field.value, result.script_text)
    chapter.updated_at = applied_at
    application = ScriptTaskApplication(
        id=uuid.uuid4().hex,
        task_id=task.id,
        chapter_id=chapter.id,
        target_field=request.target_field.value,
        idempotency_key=request.idempotency_key,
        expected_chapter_updated_at=_timestamp_key(request.expected_chapter_updated_at),
        chapter_updated_at=applied_at,
    )
    db.add(application)
    try:
        await db.flush()
    except IntegrityError as exc:
        # MySQL 的 chapter 行锁覆盖同章竞争；唯一约束继续兜住跨事务/跨章的
        # task 或 idempotency key 竞争。回滚本次章节写入后返回胜出的稳定记录。
        await db.rollback()
        winner = (
            await db.execute(
                select(ScriptTaskApplication).where(
                    (ScriptTaskApplication.task_id == request.task_id)
                    | (ScriptTaskApplication.idempotency_key == request.idempotency_key)
                )
            )
        ).scalars().first()
        if winner is not None and _same_application_request(
            winner,
            chapter_id=chapter_id,
            request=request,
        ):
            return _application_read(winner, replay=True)
        if winner is not None and winner.idempotency_key == request.idempotency_key:
            raise HTTPException(status_code=409, detail="IDEMPOTENCY_KEY_CONFLICT") from exc
        raise HTTPException(status_code=409, detail="TASK_RESULT_ALREADY_APPLIED") from exc
    return _application_read(application, replay=False)
