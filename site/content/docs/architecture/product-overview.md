---
title: "产品概览与生产链路"
weight: 2
description: "元风 当前已实现的产品边界、核心对象、状态语义与端到端生产链路。"
---

> 本文记录当前真实生效的产品抽象，不定义待实现需求。后续目标与交付安排见[产品 PRD](/docs/plans/product-prd/)。

## 产品定位

元风 是面向 AI 短剧生产的工作台。它将剧本理解、分镜准备、资产一致性、图像/视频生成和任务追踪放在同一条可人工确认的生产链路中。

当前产品服务于个人创作者和小型制作团队的本地或私有化工作场景。系统尚未实现认证、协作权限或多租户隔离；设置页中的“管理员、操作员、访客”仅是本地界面偏好，不构成权限模型。

## 核心对象

```text
Project
  └─ Chapter
       └─ Shot ── ShotDetail / DialogLine / FrameImage
            ├─ Character（由 Actor、Costume、Prop 组合）
            ├─ Scene / Prop / Costume 等可复用资产
            └─ GenerationTask / FileItem 等生成产物与任务记录
```

| 对象 | 当前职责 |
| --- | --- |
| Project | 项目元数据、视觉风格、默认视频比例和聚合统计。 |
| Chapter | 剧本原文、精简文本、分镜数量及章节推进状态。 |
| Shot | 单个镜头的索引、剧本摘录、信息确认状态和已生成视频引用。 |
| Character / Actor / Scene / Prop / Costume | 保持项目内角色和跨镜头可复用资产的一致性。 |
| PromptTemplate | 管理剧本、资产、帧图和视频等提示词模板。 |
| GenerationTask | 长耗时任务的唯一状态来源；Celery 只执行任务。 |
| FileItem / TimelineClip | 管理媒体文件、用途关联和基础时间线素材。 |

## 标准生产链路

```mermaid
flowchart LR
  A[录入章节剧本] --> B[拆分分镜与提取]
  B --> C[分镜编辑页确认候选]
  C --> D[镜头信息确认完成]
  D --> E[分镜工作室]
  E --> F[关键帧、参考图与视频准备度]
  F --> G[图片或视频生成任务]
  G --> H[任务结果回流镜头与素材库]
  H --> I[基础时间线与剪辑]
```

### 页面职责

| 页面 | 负责内容 | 不负责内容 |
| --- | --- | --- |
| 分镜编辑页 | 资产/对白提取结果的确认、修正和镜头基础信息完善。 | 视频参数、关键帧主流程和视频执行。 |
| 分镜工作室 | 视频准备度、关键帧、参考图、视频参数和生成执行。 | 主要的提取确认流程。 |
| 任务中心 | 通用任务状态、进度、取消和回跳。 | 提示词、图文映射等业务上下文的详情。 |

## 状态语义

系统将信息确认、运行时任务和视频生成条件明确分离：

| 维度 | 来源 | 语义 |
| --- | --- | --- |
| 信息确认状态 | `shot.status` | `pending` 表示提取确认未完成；`ready` 表示确认完成。 |
| 准备页聚合状态 | `ShotPreparationStateRead` | 汇总标题、剧本摘录、镜头语言、动作拍点和候选确认等条件。 |
| 视频生成准备度 | `ShotVideoReadinessRead` | 检查时长、提示词、关键帧、模型、参考模式和进行中视频任务。 |
| 运行时任务状态 | `GenerationTask.status` | `pending`、`running`、`streaming`、`succeeded`、`failed`、`cancelled`。 |

`shot.status = ready` 只代表信息确认完成，不代表镜头可以立即生成视频。生成中的状态不写入 `shot.status`，应由任务状态与 runtime summary 表达。

## 已实现能力边界

- 剧本处理：拆分、提取、优化、精简、一致性检查及专项资产分析。
- 分镜准备：候选关联/忽略、对白接受/忽略、镜头信息编辑和准备度引导。
- 生成工作台：关键帧提示词与图片、视频提示词预览、视频准备度检查、单镜头及批量生成。
- 资产与模型：全局资产库、项目关联、提示词模板、多供应商/模型及默认设置。
- 任务与媒体：GenerationTask 状态追踪、取消、结果回流、文件管理及基础时间线。

## 当前限制

- 未实现账号、认证、授权、多租户和协作审批。
- Agent 管理、文件管理和剪辑页面仍有遗留 API 或占位能力，不是主生产链路的权威入口。
- 剪辑定位为轻量衔接能力，不替代专业非线性编辑器。
- 生产部署依赖 MySQL、Redis、对象存储和 Celery Worker；前端静态部署不能单独承载完整链路。

## 延伸阅读

- [分镜状态流转](/docs/architecture/shot-status-flow/)
- [分镜页面职责边界](/docs/architecture/shot-page-boundary/)
- [生成工作台架构](/docs/architecture/generation-workspace/)
- [任务执行架构](/docs/architecture/task-execution/)
