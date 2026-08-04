/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ProductionStepStatus } from './ProductionStepStatus';
/**
 * 分页读取的 fan-out/barrier item，不暴露 Provider 上下文。
 */
export type ProductionRunStepItemRead = {
    id: string;
    step_id: string;
    entity_type: string;
    entity_id: string;
    target_key: string;
    entity_version: (string | null);
    status: ProductionStepStatus;
    attempt: number;
    blocked_reasons: Array<Record<string, any>>;
    output_ref: Record<string, any>;
    lock_version: number;
    started_at: (string | null);
    finished_at: (string | null);
    created_at: string;
    updated_at: string;
};

