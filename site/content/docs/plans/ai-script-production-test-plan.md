---
title: "AI 剧本与自动生产工作流测试用例"
weight: 6
description: "定义工作流的测试分层、标准数据、关键用例、性能安全验证与发布门禁。"
---

> 本文对应[需求方案](/docs/plans/ai-script-production-workflow/)和[详细技术设计](/docs/plans/ai-script-production-technical-design/)。工作流仍未实现，测试应随各阶段开发同步落地。

## 1. 测试策略

采用“确定性测试作为发布门禁，真实 Provider 作为受控冒烟”的策略。

| 层级 | 环境 | 范围 |
| --- | --- | --- |
| 单元 | pytest，无外部服务 | manifest、状态机、Adapter、闸门、结果解析。 |
| Service | SQLite + fake dispatcher | 任务创建、应用结果、步骤推进、readiness 聚合。 |
| 数据库并发 | 真实 MySQL | 锁、唯一约束、乐观并发、重复通知。 |
| API | TestClient / MySQL | 鉴权、契约、错误码、幂等与版本冲突。 |
| Worker | fake Provider + 测试 DB | executor、取消、超时、终态通知。 |
| 编排集成 | MySQL + Redis + Celery | fan-out、barrier、outbox、恢复和重试。 |
| 前端组件 | Vitest + Testing Library + MSW | 向导、抽屉、闸门和错误恢复。 |
| 浏览器 E2E | Playwright + Compose | 完整用户流程。 |
| Provider 冒烟 | 沙箱/低成本配置 | 协议兼容和媒体回流。 |
| 部署回归 | VPS 等价 Compose | 初始化、升级、重启与单 Worker。 |

SQLite 不能验证 MySQL 行锁和唯一索引竞争，并发与幂等用例必须使用真实 MySQL。

## 2. 标准测试数据

### 2.1 P1：三镜头标准项目

- 现代悬疑短剧，16:9，真人都市。
- 章节约 800 字，3 个镜头。
- 人物：林夏、周警官。
- 场景：雨夜便利店、街道。
- 道具：红色雨伞、旧手机。
- S1：候选已确认、首帧就绪、readiness 通过。
- S2：一个资产候选 pending，readiness 阻断。
- S3：`skip_extraction=true`，缺少参考帧。

### 2.2 P2：批量与并发项目

- 100 个镜头。
- 70 成功、20 Provider 失败、5 readiness 阻断、5 执行期间删除。
- Run 启动后再新增 10 个镜头，验证目标冻结。

### 2.3 P3：版本冲突项目

- AI 任务启动时 `Chapter.updated_at=T1`。
- 用户编辑后变为 T2。
- 使用 T1 应用 AI 结果必须冲突，且不得覆盖 T2。

### 2.4 模型配置组合

1. text/image/video 均正常；
2. 默认模型缺失；
3. 模型 ID 不存在；
4. 模型类别错误；
5. Provider 不存在或禁用；
6. API Key 为空；
7. 能力不支持比例、时长或参考模式；
8. Run 期间切换默认模型；
9. Key 使用哨兵 `SECRET_CANARY_DO_NOT_LEAK`。

## 3. Provider 测试边界

### 3.1 必须 Fake

状态机、API、并发、barrier、reconciliation、闸门、前端、性能和普通 E2E 不访问真实 Provider。

Fake Provider 支持：

- 成功、超时、401、429、5xx；
- 非法 JSON、字段缺失、超长输出；
- 首次失败后成功；
- 延迟返回和异步轮询；
- 已完成但通知丢失；
- 相同幂等键返回同一结果。

### 3.2 真实 Provider

仅执行：

- text：生成短章节并检查结构；
- image：生成一张低成本首帧；
- video：最短时长、最低成本配置；
- 每个正式支持 Adapter 至少一条冒烟。

不对文艺质量做全文快照断言，不进入普通 PR 强制门禁。

## 4. 单元与状态机用例

| 编号 | 用例 | 预期 |
| --- | --- | --- |
| WF-U-001 | 四种 preset 展开 | 阶段、闸门和 manifest_version 正确。 |
| WF-U-002 | 启动后修改 preset | 已有 run 不变化。 |
| WF-U-003 | 合法状态迁移 | 只允许设计表中的迁移。 |
| WF-U-004 | succeeded → running | 拒绝且版本不变。 |
| WF-U-005 | resume waiting_human | 拒绝，必须 confirm。 |
| WF-U-006 | paused → running | 重新 preflight 后恢复。 |
| WF-U-007 | 重试失败步骤 | attempt+1，旧任务保留。 |
| WF-U-008 | stage_outputs 与 Step 冲突 | 以 Step/Item 重建缓存。 |
| WF-U-009 | Run 后新增镜头 | 不加入目标集合。 |
| WF-U-010 | 删除目标镜头 | Item skipped 并记录原因。 |

## 5. AI 写作与 Service 用例

| 编号 | 用例 | 预期 |
| --- | --- | --- |
| SW-S-001 | 五种写作模式组装输入 | 项目、人物与章节上下文正确且限长。 |
| SW-S-002 | 合法结构化输出 | 完整保存到 task result。 |
| SW-S-003 | 非法结构 | Task failed，章节不变。 |
| SW-S-004 | 写作完成未确认 | 不修改 `raw_text`。 |
| SW-S-005 | 版本一致时应用 | 指定字段更新并记录确认。 |
| SW-S-006 | 应用时章节已编辑 | 409，不覆盖。 |
| SW-S-007 | 重复应用 | 幂等，不重复更新。 |
| WF-S-008 | Adapter 创建现有任务 | Task、Link、binding 与 outbox 正确。 |
| WF-S-009 | 已存在镜头时 divide | 默认阻断，不隐式覆盖。 |
| WF-S-010 | Adapter 边界 | 不直接调用 Provider 或内部 HTTP。 |

## 6. API 用例

| 编号 | 用例 | 预期 |
| --- | --- | --- |
| WF-A-001 | 未登录访问 mutation | 401。 |
| WF-A-002 | 创建、列表、详情 | `ApiResponse<T>` 与 OpenAPI 一致。 |
| WF-A-003 | 同章节已有活跃 run | 409 + 冲突 run 摘要。 |
| WF-A-004 | 启动缺模型 | 返回 preflight 错误，不运行。 |
| WF-A-005 | 重复 pause/resume/cancel | 幂等，无重复派发。 |
| WF-A-006 | 过期 lock version | 409。 |
| WF-A-007 | retry 非失败步骤 | 拒绝且不创建任务。 |
| WF-A-008 | Item 不属于 Step/Run | 404，不泄漏其他实体。 |
| WF-A-009 | Run 详情 | 无完整 prompt、Provider 响应和密钥。 |
| WF-A-010 | generated client | 更新 OpenAPI 后 TypeScript 通过。 |

## 7. Worker 与投递用例

| 编号 | 用例 | 预期 |
| --- | --- | --- |
| WF-W-001 | script_write 成功 | pending→running→succeeded，结果与耗时完整。 |
| WF-W-002 | 启动前取消 | 不调用 Provider。 |
| WF-W-003 | Provider 后取消 | 安全边界停止，不应用后续产物。 |
| WF-W-004 | 超时/异常 | failed + 标准错误。 |
| WF-W-005 | 终态提交后通知 | 通知只在事务提交后发生。 |
| WF-W-006 | 通知失败 | Task 终态保留，Reconciler 可恢复。 |
| WF-W-007 | Outbox 投递失败 | 退避重试且不重复创建 Task。 |
| WF-W-008 | 重试 | 新 task_id，旧任务可审计。 |

## 8. 并发与幂等用例

以下必须在 MySQL 执行：

| 编号 | 用例 | 预期 |
| --- | --- | --- |
| WF-C-001 | 20 个请求并发创建同章节 run | 仅一个活跃 run。 |
| WF-C-002 | 20 次并发 start | 每步只创建一组任务。 |
| WF-C-003 | 同一终态通知并发 50 次 | 只结算一次。 |
| WF-C-004 | API 与 Reconciler 同时推进 | transition 仅一次。 |
| WF-C-005 | 手动任务与工作流任务竞争 | 一方成功，另一方明确冲突。 |
| WF-C-006 | confirm 与实体修改并发 | 实体修改胜出时闸门失效。 |
| WF-C-007 | Retry 与旧任务迟到成功 | 旧 attempt 不覆盖新 attempt。 |
| WF-C-008 | Cancel 与下一阶段派发 | Cancel 后无新任务。 |

## 9. Fan-out 与 barrier 用例

| 编号 | 用例 | 预期 |
| --- | --- | --- |
| WF-F-001 | 100 镜头展开 | 每个 target_key 恰好一个 item。 |
| WF-F-002 | 99 终态、1 running | Barrier 不结算。 |
| WF-F-003 | 全部成功 | Step succeeded 并推进。 |
| WF-F-004 | 成功与失败混合 | Step partial，成功产物保留。 |
| WF-F-005 | 定向重试 | 只为失败项创建新 attempt。 |
| WF-F-006 | 跳过失败项 | 原因保留，重新结算。 |
| WF-F-007 | 删除镜头 | Item skipped。 |
| WF-F-008 | 新增镜头 | 不进入当前 run。 |
| WF-F-009 | Readiness 有阻断 | 不提交视频，进入 waiting_human。 |
| WF-F-010 | Barrier 重复结算 | 下一阶段只 fan-out 一次。 |
| WF-F-011 | 全部 skipped | Step skipped，不记 succeeded。 |

## 10. 恢复与对账用例

| 编号 | 用例 | 预期 |
| --- | --- | --- |
| WF-R-001 | Task 成功但通知丢失 | 一次对账后结算。 |
| WF-R-002 | Step running、Task failed | 收敛为 failed/partial。 |
| WF-R-003 | 无活跃 Task 但有下一步 | 补发下一阶段。 |
| WF-R-004 | Task cancelled、Run running | 按策略收敛。 |
| WF-R-005 | 重复对账 | 不重复派发。 |
| WF-R-006 | 两个 Reconciler 并发 | 仅一个推进。 |
| WF-R-007 | Redis 清空并重启 Worker | 从 DB 恢复 pending。 |
| WF-R-008 | 聚合缓存损坏 | 从 Step/Item 重建。 |

## 11. 模型预检用例

| 编号 | 用例 | 预期 |
| --- | --- | --- |
| WF-M-001 | script_assist | 只要求 text。 |
| WF-M-002 | prepare_frames | 要求 text + image。 |
| WF-M-003 | controlled_video | 要求 text + image + video。 |
| WF-M-004 | 模型缺失/类别错误 | 不派发任务。 |
| WF-M-005 | Provider 禁用/缺 Key | 返回结构化错误和设置入口。 |
| WF-M-006 | 能力不支持参数 | Preflight 阻断。 |
| WF-M-007 | Run 后切换默认模型 | 下一阶段用新默认并记录快照。 |
| WF-M-008 | 派发前 Provider 被禁用 | 阶段进入可恢复失败。 |
| WF-M-009 | API 传临时 model ID | 拒绝，不能绕过系统设置。 |

## 12. 人工闸门用例

| 编号 | 用例 | 预期 |
| --- | --- | --- |
| WF-G-001 | 剧本未应用 | Script gate 不可解除。 |
| WF-G-002 | 应用后章节再次修改 | 确认失效。 |
| WF-G-003 | 候选仍 pending | Preparation gate 不可解除。 |
| WF-G-004 | 显式 skip_extraction | 按现有规则可通过。 |
| WF-G-005 | Readiness 阻断 | Video gate 不可解除。 |
| WF-G-006 | Confirm 后实体变化 | 派发前重新等待。 |
| WF-G-007 | 成功确认 | 保存 Admin、时间、版本、目标和参数。 |
| WF-G-008 | Resume 绕过 gate | 拒绝。 |

## 13. 前端组件用例

| 编号 | 用例 | 预期 |
| --- | --- | --- |
| WF-UI-001 | 四步写作向导 | 校验、返回和草稿保留正确。 |
| WF-UI-002 | 模型预检失败 | 显示问题及模型设置入口。 |
| WF-UI-003 | 写作结果对照 | 明确目标字段。 |
| WF-UI-004 | 章节版本冲突 | 保留结果并要求比较。 |
| WF-UI-005 | Run 抽屉 | 正确展示 running/waiting/partial。 |
| WF-UI-006 | 重复点击 mutation | 只发一个请求。 |
| WF-UI-007 | Preparation gate | 从编辑页确认继续。 |
| WF-UI-008 | Readiness 阻断 | 工作室显示阻断和回跳。 |
| WF-UI-009 | TaskCenter | 只显示子任务摘要。 |
| WF-UI-010 | 刷新/重新登录 | 从服务端恢复运行。 |
| WF-UI-011 | 键盘与窄屏 | 抽屉、弹窗和焦点可用。 |
| WF-UI-012 | 409/503 | 正确刷新或引导设置。 |

## 14. 端到端用例

1. `E2E-001`：创作设定 → 写作候选 → 人工应用 → preparation gate。
2. `E2E-002`：已有剧本 → divide → extract → 候选确认 → 帧提示词/帧图。
3. `E2E-003`：readiness 阻断 → 修复 → 重新确认 → 视频 fan-out。
4. `E2E-004`：暂停、刷新页面、恢复。
5. `E2E-005`：Worker 在终态提交后、通知前重启 → 对账恢复。
6. `E2E-006`：部分图片失败 → 定向重试 → barrier 成功。
7. `E2E-007`：章节版本冲突，AI 结果不覆盖。
8. `E2E-008`：取消 Run，停止派发新任务。

E2E 同时断言 UI、API 和数据库最终状态。

## 15. 性能与容量

Provider 全部 Stub：

| 编号 | 场景 | 门槛 |
| --- | --- | --- |
| WF-P-001 | 20 并发创建同章节 run | 仅一个成功，无 5xx。 |
| WF-P-002 | 100 镜头 fan-out | 5 秒内完成持久化与 outbox。 |
| WF-P-003 | Terminal notification | P95 ≤ 500ms，P99 ≤ 1s。 |
| WF-P-004 | 100 item 的 Run 详情 | P95 ≤ 300ms，item 分页。 |
| WF-P-005 | 1000 待对账 item | 一轮 ≤ 60 秒，无重复推进。 |
| WF-P-006 | VPS 单 Worker | 同时最多一个重型 Provider 调用。 |
| WF-P-007 | 100 镜头运行 | Worker RSS 不超过 384MB。 |
| WF-P-008 | 10 万 Task/Link | 查询 P95 ≤ 500ms。 |

## 16. 安全用例

- `WF-SEC-001`：新 API 均受管理员 Session 保护。
- `WF-SEC-002`：跨项目/章节/Run 访问不能泄漏实体。
- `WF-SEC-003`：密钥哨兵不出现在 API、DB 业务 JSON、日志或 DOM。
- `WF-SEC-004`：Prompt injection 不能修改控制字段或闸门状态。
- `WF-SEC-005`：输入长度、数组和 JSON 深度有限制。
- `WF-SEC-006`：错误不包含 DB URL、响应头或堆栈。
- `WF-SEC-007`：Cookie 保持 Secure/HttpOnly/SameSite。
- `WF-SEC-008`：Mutation 防重复提交。
- `WF-SEC-009`：媒体引用不能造成路径穿越或 SSRF。
- `WF-SEC-010`：任务中心不显示原始 Prompt/Provider 调试信息。

## 17. 部署回归

1. 空库初始化新表和约束。
2. 已有库升级不破坏任务、镜头和任务中心。
3. SQL 重复执行幂等。
4. Web/Worker 使用同一 MySQL、Redis 和模型配置。
5. Registry 识别 `script_write`。
6. 滚动升级期间旧任务仍可结算。
7. 重启 backend/worker 后 Reconciler 推进。
8. Redis 重启不改变 DB 终态。
9. VPS `concurrency=1/prefetch=1` 生效。
10. 登录、OpenAPI、健康检查和任务投递通过。
11. 回滚不删除已有任务与媒体。
12. 页面职责与状态语义保持不变。

## 18. 发布门禁

### PR 门禁

- P0/P1 确定性用例 100% 通过，不接受 flaky 重跑放行。
- 新模块行覆盖率 ≥ 85%，分支覆盖率 ≥ 75%。
- 状态机、闸门、并发幂等、barrier 和版本冲突全部覆盖。
- Pylint 通过。
- 前端 `tsc --noEmit` 与组件测试通过。
- OpenAPI 无未提交差异。
- 密钥泄漏扫描零命中。

### 发布候选门禁

- MySQL + Redis + Celery 集成通过。
- 8 条核心 E2E 通过。
- Worker/Redis/backend 重启恢复通过。
- 100 镜头容量目标通过。
- 空库、升级和重复初始化通过。
- 每类启用 Provider 至少一条冒烟成功。

禁止发布：

- 重复阶段派发；
- 未确认内容覆盖；
- readiness 未通过仍提交视频；
- 写入 `shot.status=generating`；
- 密钥进入响应或日志；
- 使用 resume 绕过人工闸门。
