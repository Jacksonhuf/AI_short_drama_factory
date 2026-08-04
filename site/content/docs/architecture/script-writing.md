---
title: "AI 剧本写作与显式应用"
weight: 9
description: "script_write 的五种输入模式、候选读取和章节乐观锁应用边界。"
---

## 当前能力

章节工作台提供独立的 AI 写作向导，支持五种稳定模式：

- `from_scratch`：按故事前提从零创作。
- `outline_to_chapter`：按逐行大纲生成章节正文。
- `continue_writing`：根据已有上下文和下一阶段目标续写。
- `rewrite`：根据原文和改写目标重写。
- `expand_or_compress`：根据原文及目标字数或时长扩写、压缩。

前端实现位于 `front/src/pages/aiStudio/writing/`，没有把表单、轮询和候选审阅逻辑放入
`ChapterStudio`。写作任务通过 OpenAPI generated client 创建，弹窗打开期间独立轮询
通用 task result API，并将结果收窄为标题、摘要、正文、分集大纲和审阅提示。

## 候选与章节真值边界

`script_write` 成功只产生结构化候选，不直接修改章节。用户审阅后必须明确选择：

- 应用到 `raw_text`
- 应用到 `condensed_text`

应用请求同时提交：

- `task_id`
- 目标字段
- 读取章节时获得的 `expected_chapter_updated_at`
- 每次显式应用动作的 `idempotency_key`

`ChapterRead.updated_at` 是该乐观锁的公开版本。章节在候选生成或审阅期间被其他操作修改时，
后端返回 `409 CHAPTER_MODIFIED`；前端刷新章节版本、提示用户重新核对，不自动重试覆盖。
任务已应用、幂等键冲突等其他 `409` 也保持显式反馈。

## 页面与任务中心边界

- writing 模块展示完整候选和应用动作。
- production 模块只展示运行阶段摘要，并在剧本审阅 gate 提供回到 writing 模块的入口。
- 任务中心不展示候选正文、写作约束、角色提示和改动摘要，继续保持通用轻量状态面板。
