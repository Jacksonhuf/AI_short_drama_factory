"""SQLAlchemy ORM 模型。"""

from app.core.db import Base
from app.models.base import TimestampMixin

from app.models.llm import Model, ModelSettings, Provider
from app.models.task import GenerationTask
from app.models.task_dispatch_outbox import TaskDispatchOutbox, TaskDispatchOutboxStatus
from app.models.task_links import GenerationTaskLink
from app.models.script_task_application import ScriptTaskApplication
from app.models.production_runs import (
    ChapterProductionRun,
    ChapterProductionRunStep,
    ChapterProductionRunStepItem,
    ProductionRunTaskBinding,
    ProductionRunTransition,
)
from app.models.types import FileUsageKind

from app.models.studio import (
    Actor,
    ActorImage,
    Chapter,
    Character,
    CharacterImage,
    CharacterPropLink,
    Costume,
    CostumeImage,
    FileItem,
    FileUsage,
    Project,
    Prop,
    PropImage,
    PromptTemplate,
    Scene,
    SceneImage,
    Shot,
    ShotCharacterLink,
    ShotDetail,
    ShotDialogLine,
    ShotFrameImage,
    ShotFrameType,
    ProjectActorLink,
    ProjectCostumeLink,
    ProjectPropLink,
    ProjectSceneLink,
    TimelineClip,
)

__all__ = [
    "Base",
    "TimestampMixin",
    "Project",
    "Chapter",
    "Shot",
    "ShotDetail",
    "ShotDialogLine",
    "ShotFrameImage",
    "ShotFrameType",
    "ProjectActorLink",
    "ProjectSceneLink",
    "ProjectPropLink",
    "ProjectCostumeLink",
    "ShotCharacterLink",
    "Actor",
    "Character",
    "CharacterImage",
    "CharacterPropLink",
    "ActorImage",
    "Scene",
    "SceneImage",
    "Prop",
    "PropImage",
    "Costume",
    "CostumeImage",
    "PromptTemplate",
    "FileItem",
    "FileUsage",
    "FileUsageKind",
    "TimelineClip",
    "Provider",
    "Model",
    "ModelSettings",
    "GenerationTask",
    "TaskDispatchOutbox",
    "TaskDispatchOutboxStatus",
    "GenerationTaskLink",
    "ScriptTaskApplication",
    "ChapterProductionRun",
    "ChapterProductionRunStep",
    "ChapterProductionRunStepItem",
    "ProductionRunTaskBinding",
    "ProductionRunTransition",
]
