/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ScriptApplyTargetField } from './ScriptApplyTargetField';
/**
 * 显式应用结果，供 OpenAPI 生成完整响应类型。
 */
export type AppliedScriptTaskResult = {
    application_id: string;
    task_id: string;
    chapter_id: string;
    target_field: ScriptApplyTargetField;
    chapter_updated_at: string;
    idempotent_replay: boolean;
};

