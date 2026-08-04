/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ProductionExecutionMode } from './ProductionExecutionMode';
import type { ProductionStepStatus } from './ProductionStepStatus';
/**
 * 运行详情中的轻量步骤状态。
 */
export type ProductionRunStepRead = {
    id: string;
    run_id: string;
    stage_key: string;
    step_order: number;
    adapter_version: string;
    execution_mode: ProductionExecutionMode;
    status: ProductionStepStatus;
    attempt: number;
    input_snapshot: Record<string, any>;
    target_snapshot: Record<string, any>;
    output_summary: Record<string, any>;
    blocked_reasons: Array<Record<string, any>>;
    lock_version: number;
    started_at: (string | null);
    finished_at: (string | null);
    created_at: string;
    updated_at: string;
};

