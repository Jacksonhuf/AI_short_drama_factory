/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ProductionPreset } from './ProductionPreset';
import type { ProductionRunStatus } from './ProductionRunStatus';
/**
 * 列表和 mutation 返回的运行标量。
 */
export type ProductionRunSummaryRead = {
    id: string;
    project_id: string;
    chapter_id: string;
    preset_key: ProductionPreset;
    manifest_version: string;
    manifest_snapshot: Record<string, any>;
    config_snapshot: Record<string, any>;
    input_snapshot: Record<string, any>;
    target_snapshot_hash: (string | null);
    status: ProductionRunStatus;
    current_step_id: (string | null);
    lock_version: number;
    transition_version: number;
    cancel_requested: boolean;
    error_code: (string | null);
    error_message: (string | null);
    started_at: (string | null);
    finished_at: (string | null);
    created_at: string;
    updated_at: string;
};

