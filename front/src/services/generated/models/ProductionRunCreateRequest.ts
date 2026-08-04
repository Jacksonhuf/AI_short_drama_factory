/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ProductionPreset } from './ProductionPreset';
/**
 * 创建固定 preset 运行的请求。
 */
export type ProductionRunCreateRequest = {
    preset: ProductionPreset;
    config?: Record<string, any>;
    idempotency_key: string;
};

