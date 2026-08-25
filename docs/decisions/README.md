# Architecture decisions

Decision 记录已经作出的、具有长期影响且无法只从代码或 schema 理解的选择。个人开发流程不要求
先创建 Proposal；未决思路保留在任务、Issue 或被 Git 忽略的 `.agent-work/`。

| ID | Decision | Status |
| --- | --- | --- |
| 0001 | [Workspace resource layout](0001-workspace-resource-layout.md) | Accepted |
| 0002 | [Strategy notification delivery](0002-strategy-notification-delivery.md) | Accepted |
| 0003 | [Intent lifecycle observability](0003-intent-lifecycle-observability.md) | Accepted |
| 0004 | [Conflux connection ownership](0004-conflux-connection-ownership.md) | Accepted |
| 0005 | [Conflux typed Stream、View 与 Control 运行时纳管](0005-conflux-typed-io-ownership.md) | Accepted |
| 0006 | [Contract Control 边界与 Conflux 回调入口](0006-contract-control-boundary.md) | Accepted |
| 0007 | [Strategy Agent Runtime 与受治理 Intent](0007-strategy-agent-runtime.md) | Accepted |
| 0008 | [Market Subscription Target API](0008-market-subscription-targets.md) | Accepted |
| 0009 | [Account 观察事实与查询真实性](0009-account-observed-facts-and-query-truth.md) | Accepted |
| 0010 | [Interactive CLI 产品 Section 边界](0010-interactive-cli-product-sections.md) | Superseded by 0014 |
| 0011 | [账户交易工作台与 Execution 操作模式](0011-trade-workbench-and-execution-modes.md) | Accepted |
| 0012 | [Market Provider Routes and Private Feeds](0012-market-provider-routes.md) | Accepted |
| 0013 | [Read Model and Query Naming](0013-read-model-and-query-naming.md) | Accepted |
| 0014 | [统一 Textual 工作台](0014-unified-textual-workbench.md) | Accepted |
| 0015 | [Provider 连接、凭据与账户访问](0015-provider-connections-and-account-access.md) | Accepted |
| 0016 | [模型连接与模型引用](0016-model-connections-and-model-references.md) | Superseded by 0019 |
| 0017 | [运行方案、运行实例与运行中心](0017-run-plans-instances-and-operations-center.md) | Accepted |
| 0018 | [Provider-owned Market subscription planning](0018-provider-owned-market-subscription-planning.md) | Accepted |
| 0019 | [模型服务端点与可用模型](0019-model-endpoints-and-available-models.md) | Accepted |
| 0020 | [Durable Execution algorithm runs and action-first dispatch](0020-durable-execution-algorithm-runs.md) | Accepted |
| 0021 | [Fill-driven maker-first and taker-hedge execution](0021-maker-first-taker-hedge.md) | Accepted |
| 0022 | [Explicit single-path Execution algorithm selection](0022-explicit-execution-algorithm-selection.md) | Accepted |

新增 Decision 时使用下一顺序编号，并至少写明 Status、Context、Decision 和 Consequences。Decision
一旦失效，不修改历史结论；将状态改为 Superseded，并链接替代它的新 Decision。
