/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 人工 gate 确认请求；与 resume 分离以禁止绕过业务校验。
 */
export type ProductionRunConfirmRequest = {
    expected_lock_version: number;
    idempotency_key: string;
};

