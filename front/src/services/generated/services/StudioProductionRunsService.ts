/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiResponse_PaginatedData_ProductionRunStepItemRead__ } from '../models/ApiResponse_PaginatedData_ProductionRunStepItemRead__';
import type { ApiResponse_PaginatedData_ProductionRunSummaryRead__ } from '../models/ApiResponse_PaginatedData_ProductionRunSummaryRead__';
import type { ApiResponse_ProductionRunRead_ } from '../models/ApiResponse_ProductionRunRead_';
import type { ApiResponse_ProductionRunStepItemRead_ } from '../models/ApiResponse_ProductionRunStepItemRead_';
import type { ProductionRunConfirmRequest } from '../models/ProductionRunConfirmRequest';
import type { ProductionRunCreateRequest } from '../models/ProductionRunCreateRequest';
import type { ProductionRunItemMutationRequest } from '../models/ProductionRunItemMutationRequest';
import type { ProductionRunMutationRequest } from '../models/ProductionRunMutationRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class StudioProductionRunsService {
    /**
     * 创建章节生产运行
     * 创建 draft 运行并持久化 manifest v1 快照和步骤。
     * @returns ApiResponse_ProductionRunRead_ Successful Response
     * @throws ApiError
     */
    public static createProductionRunApiV1StudioChaptersChapterIdProductionRunsPost({
        chapterId,
        requestBody,
    }: {
        chapterId: string,
        requestBody: ProductionRunCreateRequest,
    }): CancelablePromise<ApiResponse_ProductionRunRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/chapters/{chapter_id}/production-runs',
            path: {
                'chapter_id': chapterId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 列出章节生产运行
     * 按创建时间倒序列出指定章节的历史运行。
     * @returns ApiResponse_PaginatedData_ProductionRunSummaryRead__ Successful Response
     * @throws ApiError
     */
    public static listProductionRunsApiV1StudioChaptersChapterIdProductionRunsGet({
        chapterId,
        page = 1,
        pageSize = 20,
    }: {
        chapterId: string,
        page?: number,
        pageSize?: number,
    }): CancelablePromise<ApiResponse_PaginatedData_ProductionRunSummaryRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/chapters/{chapter_id}/production-runs',
            path: {
                'chapter_id': chapterId,
            },
            query: {
                'page': page,
                'page_size': pageSize,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 获取章节生产运行详情
     * 返回运行标量和步骤汇总，不展开 item、prompt 或 Provider 响应。
     * @returns ApiResponse_ProductionRunRead_ Successful Response
     * @throws ApiError
     */
    public static getProductionRunApiV1StudioProductionRunsRunIdGet({
        runId,
    }: {
        runId: string,
    }): CancelablePromise<ApiResponse_ProductionRunRead_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/production-runs/{run_id}',
            path: {
                'run_id': runId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 启动章节生产运行
     * 将 draft 运行置为 running，并通过 outbox 自动派发首步。
     * @returns ApiResponse_ProductionRunRead_ Successful Response
     * @throws ApiError
     */
    public static startProductionRunApiV1StudioProductionRunsRunIdStartPost({
        runId,
        requestBody,
    }: {
        runId: string,
        requestBody: ProductionRunMutationRequest,
    }): CancelablePromise<ApiResponse_ProductionRunRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/production-runs/{run_id}/start',
            path: {
                'run_id': runId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 暂停章节生产运行
     * 暂停 running 运行。
     * @returns ApiResponse_ProductionRunRead_ Successful Response
     * @throws ApiError
     */
    public static pauseProductionRunApiV1StudioProductionRunsRunIdPausePost({
        runId,
        requestBody,
    }: {
        runId: string,
        requestBody: ProductionRunMutationRequest,
    }): CancelablePromise<ApiResponse_ProductionRunRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/production-runs/{run_id}/pause',
            path: {
                'run_id': runId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 恢复章节生产运行
     * 仅恢复 paused 运行；人工等待状态不走此入口。
     * @returns ApiResponse_ProductionRunRead_ Successful Response
     * @throws ApiError
     */
    public static resumeProductionRunApiV1StudioProductionRunsRunIdResumePost({
        runId,
        requestBody,
    }: {
        runId: string,
        requestBody: ProductionRunMutationRequest,
    }): CancelablePromise<ApiResponse_ProductionRunRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/production-runs/{run_id}/resume',
            path: {
                'run_id': runId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 取消章节生产运行
     * 取消非终态运行并释放同章节活跃槽。
     * @returns ApiResponse_ProductionRunRead_ Successful Response
     * @throws ApiError
     */
    public static cancelProductionRunApiV1StudioProductionRunsRunIdCancelPost({
        runId,
        requestBody,
    }: {
        runId: string,
        requestBody: ProductionRunMutationRequest,
    }): CancelablePromise<ApiResponse_ProductionRunRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/production-runs/{run_id}/cancel',
            path: {
                'run_id': runId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 确认章节生产运行人工闸门
     * 执行 gate 业务校验后推进；waiting_human 不能通过 resume 绕过。
     * @returns ApiResponse_ProductionRunRead_ Successful Response
     * @throws ApiError
     */
    public static confirmProductionRunStepApiV1StudioProductionRunsRunIdStepsStepIdConfirmPost({
        runId,
        stepId,
        requestBody,
    }: {
        runId: string,
        stepId: string,
        requestBody: ProductionRunConfirmRequest,
    }): CancelablePromise<ApiResponse_ProductionRunRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/production-runs/{run_id}/steps/{step_id}/confirm',
            path: {
                'run_id': runId,
                'step_id': stepId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 分页列出生产步骤项
     * 分页返回冻结 item、结算状态和结构化阻断原因。
     * @returns ApiResponse_PaginatedData_ProductionRunStepItemRead__ Successful Response
     * @throws ApiError
     */
    public static listProductionRunStepItemsApiV1StudioProductionRunsRunIdStepsStepIdItemsGet({
        runId,
        stepId,
        page = 1,
        pageSize = 20,
    }: {
        runId: string,
        stepId: string,
        page?: number,
        pageSize?: number,
    }): CancelablePromise<ApiResponse_PaginatedData_ProductionRunStepItemRead__> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/studio/production-runs/{run_id}/steps/{step_id}/items',
            path: {
                'run_id': runId,
                'step_id': stepId,
            },
            query: {
                'page': page,
                'page_size': pageSize,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 重试生产步骤项
     * 仅重试指定失败项，不重跑已成功项。
     * @returns ApiResponse_ProductionRunStepItemRead_ Successful Response
     * @throws ApiError
     */
    public static retryProductionRunStepItemApiV1StudioProductionRunsRunIdStepsStepIdItemsItemIdRetryPost({
        runId,
        stepId,
        itemId,
        requestBody,
    }: {
        runId: string,
        stepId: string,
        itemId: string,
        requestBody: ProductionRunItemMutationRequest,
    }): CancelablePromise<ApiResponse_ProductionRunStepItemRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/production-runs/{run_id}/steps/{step_id}/items/{item_id}/retry',
            path: {
                'run_id': runId,
                'step_id': stepId,
                'item_id': itemId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * 跳过生产步骤项
     * 排除指定失败项并重新结算 barrier。
     * @returns ApiResponse_ProductionRunStepItemRead_ Successful Response
     * @throws ApiError
     */
    public static skipProductionRunStepItemApiV1StudioProductionRunsRunIdStepsStepIdItemsItemIdSkipPost({
        runId,
        stepId,
        itemId,
        requestBody,
    }: {
        runId: string,
        stepId: string,
        itemId: string,
        requestBody: ProductionRunItemMutationRequest,
    }): CancelablePromise<ApiResponse_ProductionRunStepItemRead_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/studio/production-runs/{run_id}/steps/{step_id}/items/{item_id}/skip',
            path: {
                'run_id': runId,
                'step_id': stepId,
                'item_id': itemId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
