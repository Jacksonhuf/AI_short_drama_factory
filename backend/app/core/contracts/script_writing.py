"""AI 剧本写作的跨层输入输出契约。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScriptWriteMode(str, Enum):
    """写作模式；稳定值同时用于 API、任务 payload 和提示词。"""

    from_scratch = "from_scratch"
    outline_to_chapter = "outline_to_chapter"
    continue_writing = "continue_writing"
    rewrite = "rewrite"
    expand_or_compress = "expand_or_compress"


class ScriptGenre(str, Enum):
    """常用题材；other 允许通过 additional_instructions 补充细分题材。"""

    drama = "drama"
    comedy = "comedy"
    romance = "romance"
    suspense = "suspense"
    action = "action"
    fantasy = "fantasy"
    science_fiction = "science_fiction"
    other = "other"


class ScriptTone(str, Enum):
    """剧本整体语气。"""

    light = "light"
    serious = "serious"
    warm = "warm"
    dark = "dark"
    tense = "tense"
    humorous = "humorous"
    other = "other"


class ScriptCharacterInput(BaseModel):
    """写作上下文中的精简人物信息，避免复制完整资产记录。"""

    model_config = ConfigDict(extra="forbid")

    entity_id: str | None = Field(None, max_length=64)
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field("", max_length=2000)
    current_state: str | None = Field(None, max_length=1000)


class ScriptWriteRequest(BaseModel):
    """五种写作模式的统一请求契约。"""

    model_config = ConfigDict(extra="forbid")

    mode: ScriptWriteMode
    project_id: str = Field(..., min_length=1, max_length=64)
    chapter_id: str | None = Field(None, min_length=1, max_length=64)
    premise: str = Field(..., min_length=1, max_length=10000)
    genre: ScriptGenre = ScriptGenre.drama
    tone: ScriptTone = ScriptTone.serious
    audience: str | None = Field(None, max_length=500)
    visual_style: str | None = Field(None, max_length=500)
    episode_count: int | None = Field(None, ge=1, le=500)
    chapter_index: int | None = Field(None, ge=1, le=10000)
    target_duration_seconds: int | None = Field(None, ge=10, le=14400)
    target_length: int | None = Field(None, ge=100, le=100000)
    characters: list[ScriptCharacterInput] = Field(default_factory=list, max_length=100)
    world_setting: str | None = Field(None, max_length=10000)
    episode_outline: list[str] = Field(default_factory=list, max_length=500)
    previous_context: str | None = Field(None, max_length=50000)
    source_text: str | None = Field(None, max_length=100000)
    rewrite_goal: str | None = Field(None, max_length=5000)
    next_stage_goal: str | None = Field(None, max_length=5000)
    must_include: list[str] = Field(default_factory=list, max_length=100)
    must_avoid: list[str] = Field(default_factory=list, max_length=100)
    additional_instructions: str | None = Field(None, max_length=5000)

    @model_validator(mode="after")
    def validate_mode_inputs(self) -> "ScriptWriteRequest":
        """按模式约束必要上下文，尽早拒绝无法执行的写作请求。"""

        if self.mode is ScriptWriteMode.outline_to_chapter:
            if not self.chapter_id or not self.episode_outline:
                raise ValueError("outline_to_chapter requires chapter_id and episode_outline")
        elif self.mode is ScriptWriteMode.continue_writing:
            if not self.chapter_id or not self.previous_context or not self.next_stage_goal:
                raise ValueError(
                    "continue_writing requires chapter_id, previous_context and next_stage_goal"
                )
        elif self.mode is ScriptWriteMode.rewrite:
            if not self.chapter_id or not self.source_text or not self.rewrite_goal:
                raise ValueError("rewrite requires chapter_id, source_text and rewrite_goal")
        elif self.mode is ScriptWriteMode.expand_or_compress:
            if not self.chapter_id or not self.source_text or not (
                self.target_length or self.target_duration_seconds
            ):
                raise ValueError(
                    "expand_or_compress requires chapter_id, source_text and a target size"
                )
        return self


class ScriptEpisodeOutlineItem(BaseModel):
    """单集大纲结果。"""

    model_config = ConfigDict(extra="forbid")

    episode: int = Field(..., ge=1, le=500)
    title: str = Field(..., min_length=1, max_length=255)
    summary: str = Field(..., min_length=1, max_length=5000)


class ScriptWriteResult(BaseModel):
    """五种模式共用的结构化候选结果；正文只能经显式 apply 写入章节。"""

    model_config = ConfigDict(extra="forbid")

    mode: ScriptWriteMode
    title: str = Field(..., min_length=1, max_length=255)
    summary: str = Field(..., min_length=1, max_length=10000)
    script_text: str = Field(..., min_length=1, max_length=100000)
    episode_outline: list[ScriptEpisodeOutlineItem] = Field(default_factory=list, max_length=500)
    character_notes: list[str] = Field(default_factory=list, max_length=100)
    continuity_notes: list[str] = Field(default_factory=list, max_length=100)
    assumptions: list[str] = Field(default_factory=list, max_length=100)
    review_questions: list[str] = Field(default_factory=list, max_length=100)
    change_summary: list[str] = Field(default_factory=list, max_length=100)


class ScriptApplyTargetField(str, Enum):
    """允许显式覆盖的章节文本字段白名单。"""

    raw_text = "raw_text"
    condensed_text = "condensed_text"


class ApplyScriptTaskResultRequest(BaseModel):
    """将成功写作任务候选应用到章节的乐观锁请求。"""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(..., min_length=1, max_length=64)
    target_field: ScriptApplyTargetField
    expected_chapter_updated_at: datetime
    idempotency_key: str = Field(..., min_length=1, max_length=128)


class AppliedScriptTaskResult(BaseModel):
    """显式应用结果，供 OpenAPI 生成完整响应类型。"""

    application_id: str
    task_id: str
    chapter_id: str
    target_field: ScriptApplyTargetField
    chapter_updated_at: datetime
    idempotent_replay: bool
