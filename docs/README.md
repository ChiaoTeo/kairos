# Kairos documentation

正式文档只保存需要长期维护的项目知识。Agent 的临时分析、实施计划和生成报告位于
`.agent-work/`，不会提交到 Git，也不能作为正式文档的依赖。

## Guides

面向使用者，回答“如何完成一项操作”。

- [项目与运行操作](guides/operations.md)
- [CLI 场景导览](guides/cli-scenarios.md)
- [策略通知](guides/strategy-notifications.md)

## Architecture

描述系统当前生效的结构、边界和维护规则。

- [架构文档索引](architecture/README.md)
- [Capital management boundary](architecture/capital-management.md)
- [CLI boundary](architecture/cli-boundary.md)
- [Cargo 依赖管理规范](architecture/cargo-dependency-management.md)

业务模块的专属边界优先记录在所属 crate 的 README，例如
[`Reference` module](../crates/modules/reference/README.md)。

## Integrations

记录 Provider 能力、当前覆盖、外部来源和许可证证据。

- [Integration 文档索引](integrations/README.md)
- [Integration capability matrix](integrations/capability-matrix.md)
- [Adapter provenance](integrations/adapter-provenance/README.md)

## Decisions

只记录已经作出、具有长期架构意义且无法仅从代码理解的决定。

- [Decision 索引](decisions/README.md)
- [0001: Workspace resource layout](decisions/0001-workspace-resource-layout.md)
- [0002: Strategy notification delivery](decisions/0002-strategy-notification-delivery.md)
- [0003: Intent lifecycle observability](decisions/0003-intent-lifecycle-observability.md)

## Sources of truth

| Information | Authoritative source |
| --- | --- |
| Repository architecture, ownership and Agent rules | [`AGENTS.md`](../AGENTS.md) |
| Wire contracts and protocol registry | [`schemas/`](../schemas/README.md) |
| Module public boundary and local architecture | Owning crate README and application API |
| User workflows | `docs/guides/` |
| Current Provider coverage and upstream evidence | `docs/integrations/` |
| Rationale for accepted architecture choices | `docs/decisions/` |
| Generated API pages | Local `target/docs/api/`; never committed |

## Lifecycle

- 日常任务不创建 Proposal 或设计文档。
- 未决设计留在任务对话、Issue 或 `.agent-work/<task>/`。
- 已作出的重要决定直接提炼为 Decision，不保存完整实施日志。
- 代码或 schema 已能完整表达的事实不再复制成 Markdown。
- 操作、现状或决定变化时，同一变更必须更新对应 Guide、Architecture、Integration 或 Decision。
- 可重复生成的 HTML、索引和报告输出到 `target/`，由 CI 按需发布 artifact。
