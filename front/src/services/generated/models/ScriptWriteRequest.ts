/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ScriptCharacterInput } from './ScriptCharacterInput';
import type { ScriptGenre } from './ScriptGenre';
import type { ScriptTone } from './ScriptTone';
import type { ScriptWriteMode } from './ScriptWriteMode';
/**
 * 五种写作模式的统一请求契约。
 */
export type ScriptWriteRequest = {
    mode: ScriptWriteMode;
    project_id: string;
    chapter_id?: (string | null);
    premise: string;
    genre?: ScriptGenre;
    tone?: ScriptTone;
    audience?: (string | null);
    visual_style?: (string | null);
    episode_count?: (number | null);
    chapter_index?: (number | null);
    target_duration_seconds?: (number | null);
    target_length?: (number | null);
    characters?: Array<ScriptCharacterInput>;
    world_setting?: (string | null);
    episode_outline?: Array<string>;
    previous_context?: (string | null);
    source_text?: (string | null);
    rewrite_goal?: (string | null);
    next_stage_goal?: (string | null);
    must_include?: Array<string>;
    must_avoid?: Array<string>;
    additional_instructions?: (string | null);
};

