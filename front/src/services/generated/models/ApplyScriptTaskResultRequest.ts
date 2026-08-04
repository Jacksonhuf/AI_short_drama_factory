/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ScriptApplyTargetField } from './ScriptApplyTargetField';
/**
 * 将成功写作任务候选应用到章节的乐观锁请求。
 */
export type ApplyScriptTaskResultRequest = {
    task_id: string;
    target_field: ScriptApplyTargetField;
    expected_chapter_updated_at: string;
    idempotency_key: string;
};

