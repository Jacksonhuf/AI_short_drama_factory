---
title: "LLM 默认模型解析"
weight: 35
description: "当前生效的 LLM 默认模型来源与解析顺序。"
---

本文记录当前真实生效的默认模型规则（text / image / video）。

## 单一事实来源

- 默认模型统一由 `model_settings` 单例表维护：
  - `default_text_model_id`
  - `default_image_model_id`
  - `default_video_model_id`
- `models` 表不再承担“默认模型”语义，`models.is_default` 已下线。

## 解析规则

- 运行时按类别读取 `model_settings` 对应字段。
- 若对应默认模型 ID 未配置，服务返回 `503`（`No default model configured for category=...`）。
- 若配置了模型 ID 但模型不存在，服务返回 `503`（`Configured default model not found: ...`）。
- `script_write` worker 与其他文本处理 executor 一样，通过
  `build_default_text_llm_sync` 读取 `default_text_model_id`；写作 API 不接受临时
  model ID 绕过该设置。
- `script_write` 独立任务在创建 `GenerationTask`、关联和 outbox 前执行本地预检：
  默认文本模型必须存在，其 Provider 必须启用，并按 Provider 注册声明提供所需
  API Key。预检不访问供应商网络；失败返回 `503`，不留下任务或 outbox。
- production run 在 `start` 状态变更前按 preset 预检所需类别：
  所有 preset 检查 text，`prepare_frames` / `controlled_video` 追加 image，
  `controlled_video` 再追加 video。图片和视频同时使用当前 preset 的
  `video_ratio` 走既有 capability/options 校验。失败时 run 保持 `draft`，不派发首步。

## 管理入口

- 默认模型仅通过 `LLM Model Settings` 接口维护（`/api/v1/llm/model-settings`）。
- 模型列表（`/api/v1/llm/models`）仅维护模型实体信息（名称、类别、供应商、参数等），不再提供默认切换语义。
