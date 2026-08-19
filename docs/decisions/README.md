# Architecture decisions

Decision 记录已经作出的、具有长期影响且无法只从代码或 schema 理解的选择。个人开发流程不要求
先创建 Proposal；未决思路保留在任务、Issue 或被 Git 忽略的 `.agent-work/`。

| ID | Decision | Status |
| --- | --- | --- |
| 0001 | [Workspace resource layout](0001-workspace-resource-layout.md) | Accepted |
| 0002 | [Strategy notification delivery](0002-strategy-notification-delivery.md) | Accepted |
| 0003 | [Intent lifecycle observability](0003-intent-lifecycle-observability.md) | Accepted |
| 0004 | [Conflux connection ownership](0004-conflux-connection-ownership.md) | Accepted |

新增 Decision 时使用下一顺序编号，并至少写明 Status、Context、Decision 和 Consequences。Decision
一旦失效，不修改历史结论；将状态改为 Superseded，并链接替代它的新 Decision。
