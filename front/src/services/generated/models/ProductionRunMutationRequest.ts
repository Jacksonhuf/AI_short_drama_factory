/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * 所有生命周期 mutation 共享的乐观锁和幂等参数。
 */
export type ProductionRunMutationRequest = {
    expected_lock_version: number;
    idempotency_key: string;
};

