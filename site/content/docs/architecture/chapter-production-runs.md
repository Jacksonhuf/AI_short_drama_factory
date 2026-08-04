---
title: "章节生产运行状态架构"
weight: 8
description: "ChapterProductionRun 的持久化模型、fan-out/barrier、人工闸门和恢复边界。"
---

## 当前范围

章节生产运行当前具备四个固定 preset 的可恢复编排：

- `ChapterProductionRun` 保存章节归属、固定 preset、manifest v1 完整快照、运行状态和乐观锁版本。
- `ChapterProductionRunStep` 保存展开后的有序步骤。
- `ProductionRunTaskBinding` 将线性步骤或 item 的每次尝试绑定到不可覆盖的 `GenerationTask`。
- `ChapterProductionRunStepItem` 保存 fan-out/barrier 首次进入时冻结的镜头目标和每次结算事实。
- `ProductionRunTransition` 同时承担状态审计和 mutation 幂等结果持久化。
- `TaskDispatchOutbox` 继续使用现有实现，没有重复定义。

- StageAdapter registry 还注册 `script_simplify`、`script_consistency`、
  `script_optimize`、`frame_prompt`、`frame_image`、`video_readiness`、
  `video_submit_gate` 和 `video_generation`；adapter 只调用现有 task service/executor。
- `start` 在同一事务创建首步任务、binding 和 outbox；API 显式提交后按 run
  查询 due outbox 并立即调用现有 dispatcher。
- `start` 写入状态前按 preset 执行纯本地模型预检：所有 preset 需要 text，
  `prepare_frames` / `controlled_video` 还需要 image，`controlled_video` 还需要
  video。默认模型、启用 Provider、所需 API Key 或画幅能力不满足时返回 `503`；
  run 保持 `draft`，不创建首步任务或 outbox。
- `resume`、gate `confirm` 使用相同的“提交后快速投递”路径；publish 永远不在
  未提交业务事务或读取事务中执行。
- terminal notification 持久化结算 binding/step，并在 run 仍为 `running`
  时创建下一步；结算事务提交后立即投递该 run 的 due outbox。
- 即时投递失败只更新 outbox 的失败状态和退避时间，不回滚已提交工作流事实；
  stale outbox reconciliation 继续作为恢复兜底。
- Reconciler 修复终态通知丢失，以及 `running` 但没有活动任务的停滞运行。
- 帧提示词、帧图和视频任务均在 run 事务中写入现有 `TaskDispatchOutbox`；单
  Worker 部署继续由 Worker 自身串行消费，编排器不主动提高并发。

## 固定 manifest v1

后端只接受四个已可运行的 preset，不接受调用方上传任意步骤图：

| preset | 固定步骤 |
| --- | --- |
| `script_assist` | `script_write` → `script_review_gate` |
| `prepare_shots` | 可选文本处理 → `script_divide` → `script_extract` → `human_preparation_gate` |
| `prepare_frames` | 可选文本处理 → `prepare_shots` → `frame_prompt` → `frame_image` → `video_readiness` |
| `controlled_video` | 可选文本处理 → `prepare_frames` → `video_submit_gate` → `video_generation` |

创建 run 时会复制完整 manifest 快照。服务内的 preset 定义后续变化不会修改已有运行。
三个准备类 preset 接受 `simplify_script`、`check_consistency` 和 `optimize_script`
布尔配置。启用的文本节点固定按 `script_simplify` → `script_consistency` →
`script_optimize` 排列；启用优化会自动包含一致性检查。它们是用户启动自动准备时
显式选择的自动中间步骤，不增加人工应用闸门，后续分镜仍必须通过
`human_preparation_gate`。

文本结果只沿当前 run 的 binding 传递：一致性与优化优先读取精简结果，否则读取
章节文本；优化同时读取一致性任务结果；分镜拆分按优化结果、精简结果、章节文本的
顺序选择输入。提取阶段继续读取拆分结果，并在存在时携带同一次运行的一致性结果。
三个文本 adapter 为新任务原子写入 outbox；若现有 task service 返回了不属于本次
run 的活动任务，则以 `ACTIVE_TASK_CONFLICT` 终止当前阶段，不重复投递或绑定该任务。
受控生成默认 `frame_types=["first"]`、`reference_mode="first"`、
`video_ratio="16:9"`；调用方只能覆盖已声明的合法值。

## Fan-out 与 barrier

- 首次进入集合阶段时把当前章节 shot ID 写入 run 输入快照并计算摘要；之后新增
  shot 不会加入该 run。
- 每个 fan-out target 创建独立 item、GenerationTask、binding 和 outbox。单项
  失败不回滚成功项。
- 冻结目标已删除时保留 item，并以 `TARGET_DELETED` 结算为 `skipped`。
- 所有 item 到达终态后才结算 step：全成功为 `succeeded`，全跳过为 `skipped`，
  成功/跳过与失败并存为 `partial`，全部失败为 `failed`。
- `partial/failed` 将 run 置为 `waiting_human`；管理员可通过 item `retry` 或
  `skip` 精确恢复。旧 attempt 的迟到通知只结算历史 binding，不覆盖当前 item。
- terminal notification 以 binding 的 `notified_at` 去重；step 只有一次从运行态
  进入终态，因此下一阶段只推进一次。
- `video_readiness` 对每个冻结镜头调用统一 readiness 服务。阻断项进入
  `waiting_human`，修复后 retry 会实时重算。
- `video_submit_gate` 必须显式 confirm；确认时会对每个仍存在的冻结镜头重新调用
  readiness 服务，避免 barrier 通过后的实体变化绕过校验。任一镜头不再 ready 或
  当前没有可提交目标都会阻断。确认前不会创建任何视频任务。视频 executor 继续
  使用原有文件、镜头和素材引用回流逻辑。

## 状态与一致性

当前公开 mutation 为：

```text
draft → running → paused → running
running → waiting_human → running / succeeded
draft/running/waiting_human/paused/failed → cancelled
```

- `TransitionService` 是 run 状态的唯一写入口。
- 每次 mutation 必须提交 `expected_lock_version` 和 `idempotency_key`。
- 版本不一致返回 `409 RUN_VERSION_CONFLICT`。
- 相同 key、相同请求重放首次持久化结果；相同 key、不同请求返回 `409 IDEMPOTENCY_KEY_CONFLICT`。
- 非法状态迁移返回 `409 INVALID_RUN_TRANSITION`。
- mutation 按 run、当前 step 的顺序加锁；持锁期间不调用模型或 Provider。
- `pause` 和 `cancel` 后的迟到 terminal notification 只结算已有事实，不派发下一步。

## 人工闸门

- `script_review_gate` 仅在本次 run 的 `script_write` binding 已存在
  `ScriptTaskApplication` 时通过。
- `human_preparation_gate` 读取当前章节镜头列表，并逐镜头调用统一
  preparation-state 服务；无镜头或任一镜头未准备完成都会拒绝确认。目标集合在
  随后的首个 fan-out 阶段冻结。
- gate 必须调用 step `confirm` API。`resume` 只接受 `paused`，不能绕过
  `waiting_human`。
- 确认保存 actor、时间、目标快照和 snapshot hash。

## 同章节单活跃运行

活跃状态为：

```text
draft / running / waiting_human / paused / failed
```

MySQL 迁移和 SQLite ORM 建表都通过生成列 `active_chapter_id` 实现唯一约束。终态生成 `NULL`，因此历史终态可并存；活跃状态生成 `chapter_id`，同一章节只能占用一个槽。SQLite 创建流程会在读取前取得写锁，数据库唯一约束仍是最终裁决。

## API

管理员鉴权继续由全局 `AdminSessionMiddleware` 负责。Studio 路由统一返回 typed `ApiResponse<T>`：

```text
POST /api/v1/studio/chapters/{chapter_id}/production-runs
GET  /api/v1/studio/chapters/{chapter_id}/production-runs
GET  /api/v1/studio/production-runs/{run_id}
POST /api/v1/studio/production-runs/{run_id}/start
POST /api/v1/studio/production-runs/{run_id}/pause
POST /api/v1/studio/production-runs/{run_id}/resume
POST /api/v1/studio/production-runs/{run_id}/cancel
POST /api/v1/studio/production-runs/{run_id}/steps/{step_id}/confirm
GET  /api/v1/studio/production-runs/{run_id}/steps/{step_id}/items
POST /api/v1/studio/production-runs/{run_id}/steps/{step_id}/items/{item_id}/retry
POST /api/v1/studio/production-runs/{run_id}/steps/{step_id}/items/{item_id}/skip
```

详情返回有序步骤摘要；item 通过独立分页 API 读取，不返回 prompt、Provider 原始
响应或凭据。

## 前端运行控制

章节工作台的“自动生产”入口当前支持创建并启动全部四个 preset。三个准备类 preset
提供精简、一致性检查和优化三个可选开关，并把归一化配置固化到运行快照。
`prepare_frames`
和 `controlled_video` 会显式提交 `frame_types`、`reference_mode` 与
`video_ratio`，后端继续负责合法值归一化。前端结构保持独立：

- `front/src/pages/aiStudio/production/ProductionRunDrawer.tsx` 展示阶段、运行状态、
  模型和错误摘要，并提供 `start / pause / resume / cancel`。
- 创建或启动失败时直接展示后端 `message`；模型预检返回 `503` 时同时提供模型管理
  入口，便于修复默认模型、Provider 状态或 API Key。
- fan-out/barrier 进入 `partial/failed` 后，`ProductionStepItemsPanel` 分页读取冻结
  item，逐镜头展示结构化阻断原因，并只对当前失败项开放定向 `retry/skip`。
- `video_readiness` 阻断时，修复入口指向分镜工作室；业务详情不复制到运行抽屉。
- `video_submit_gate` 展示冻结镜头数、参考模式和视频比例，只有用户显式 confirm
  后才会推进 `video_generation`。
- `useProductionRuns.ts` 通过章节 run 列表发现活跃运行，再独立轮询 run 详情；该轮询
  不写入通用任务中心。
- `script_assist` 到达 `script_review_gate` 后，抽屉只提供前往写作候选审阅的入口，
  候选正文仍在 writing 模块中展示和应用。
- `prepare_shots` 到达 `human_preparation_gate` 后，分镜编辑页显示“确认准备完成并继续”；
  确认调用 step `confirm` API，后端再次校验本章所有镜头的 preparation state。

任务中心继续只展示通用任务状态、进度和回跳等轻量信息，不承载 production step
业务快照、写作候选或 preparation gate 的业务细节。
