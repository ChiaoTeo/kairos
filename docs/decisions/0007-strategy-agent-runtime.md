# Decision 0007：Strategy Agent Runtime 与受治理 Intent

- Status: Accepted
- Date: 2026-08-21
- Scope: Python Strategy runtime、Execution Intent admission、Agent/MCP 运行时

## Context

Strategy 通过 typed `ctx.execution` 命令提交 Intent。项目需要让大模型在提交前审核或收紧 Intent，
并允许不同 Strategy 提供各自的目标、思路和上下文；未来还可能出现订阅建议、风险收紧或暂停建议。
因此这个能力不能被建模成只处理 Intent 的小型 controller，也不能让 Strategy 自己管理模型 session、
tool loop、decision lifecycle 或 SDK 资源。

大模型输出不是业务事实。Execution 仍拥有 Intent 与订单生命周期，Risk 仍拥有 authorization，Account 与
Market 仍拥有权威状态。Agent 必须位于这些 owner 之前的治理边界，且不能通过 tool 绕过 owner Application
或 Contract。

Strategy callback 是同步、串行的业务入口。模型和 MCP I/O 不能阻塞 callback；Strategy 也不需要接收并
确认修改后的 Intent。回测必须可复现，不能依赖远程模型、墙上时钟或后台线程调度。

## Decision

### Strategy-scoped runtime

每个 Strategy process 可选装配一个 Strategy-scoped Agent runtime。Launch Profile 固定其静态上限：

- enabled/required、runtime、固定 model snapshot 与 credential reference；
- Profile goal、rubric、invalidation rules 与 reason/risk code allowlist；
- operation scope、初始模式、Strategy 可切换模式；
- failure policy、revision policy、queue/run limits；
- 只读 MCP server/profile 的非 secret 快照与 content hash。

Strategy 不能动态替换这些上限。paper/live 第一次启用必须从 shadow 开始；backtest 只能使用 fixture runtime。

### Strategy API

Strategy 只有两个 Agent 入口：

- `ctx.agent.publish_context(document)`：按 key 完整替换一个 bounded、immutable context document；
- `ctx.agent.remove_context(key)`：撤回 document；
- `ctx.agent.set_mode(mode)`：在 Launch allowlist 内切换 shadow/gate/revise；
- `on_agent(ctx, event)`：best-effort 接收最小终态通知。

前三个命令只更新本地 context/mode state，不触发模型、MCP 或业务写。candidate admission 固定当时的
context snapshot、watermark、mode 与 mode revision；之后的更新不影响 queued/in-flight run。

`on_agent` 仅暴露 decision ID、capability、终态和 Profile 允许的 reason codes。它不包含 original/effective
Intent、revision、prompt、tool payload 或原始模型输出，也不是审批回执。worker 先完成本地 policy 与下游
提交，再把事件交给 Strategy ingress；`on_agent` 的返回或失败不能回滚 Execution。

Strategy API 不暴露 launch、submit candidate、decision query、history、health、Runner、session 或 tool API。
健康状态只通过 Launch/System diagnostics 暴露。

### Governed Intent path

开启 Intent review 后，现有 typed `ctx.execution` 命令由 concrete adapter 装饰：

```text
Strategy typed Intent
  -> immutable candidate + persisted Decision record
  -> bounded single-owner worker
  -> OpenAI Agents SDK + approved read-only MCP
  -> strict DecisionResult
  -> deterministic local revision policy
  -> effective typed Intent
  -> Execution validation and admission audit
  -> Risk authorization and normal Execution lifecycle
```

Strategy callback 只做本地校验、snapshot、persist 与 non-blocking enqueue。worker 是 queue、in-flight run、
SDK/MCP session、Decision store、health 和 shutdown 的唯一 mutable owner。当前只有一个生产 runtime，因而不
新增 Kairos-owned provider trait；test fake/fixture 不构成第二个生产实现。

模型只返回 approve/reject/revise/abstain 的严格结构。它不能调用写 tool。revise 使用封闭的 typed revision
union，并由本地 policy 原子应用；只能降低 quantity、收紧价格/期限/slippage/split policy 或要求 maker，
不得改变 identity、account、instrument、route、direction、Intent type、leg identity 或扩大风险。最终结果
仍重新经过 Execution validation 与 Risk authorization。

### Modes and failure policy

- **shadow**：original 立即沿原路径提交；Agent 只记录对照结果，绝不二次提交。
- **gate**：approve 提交 original；reject 不提交；revise 视为 abstain。
- **revise**：approve 提交 original；有效 revise 提交 rebuilt effective Intent；reject 不提交。

queue full、timeout、invalid output、required context/MCP unavailable 或 runtime outage 时，对新增或无法判断的
敞口 fail closed。只有 Account 的 fresh、complete 权威 current view 能证明是降低绝对敞口时，才允许沿 direct
Execution path 绕过 Agent。绕过不能伪造 approved admission evidence；Agent failure 与下游降险成功分别记录。
final command 已开始但 delivery certainty 未知时标记 `submission_indeterminate`，不得自动 retry。

### Tools and model runtime

第一生产 runtime 使用可选的 OpenAI Agents SDK，复用其 structured output、model/tool loop、MCP session、
run limit 与 tracing。model 必须是带日期的 snapshot ID 或稳定 fine-tuned ID；可漂移 alias 在 Launch 校验时
拒绝。trace 不含敏感输入，provider store 关闭，并限制 timeout、turn、tool call、input/output budget。

MCP 只允许显式 allowlist 中的 owner read capability，并按 workspace/launch/instance/strategy/account scope
收窄。host 校验结果大小、行数、freshness 与 credential-like 字段。required MCP 失败进入 failure policy；
optional MCP 只向模型返回固定的脱敏 unavailable marker，并在 Decision 中保存 hash/status evidence。禁止
Execution/Risk/Market/Account mutation、shell、filesystem write、raw SQL、任意 URL 和 provider request。

### Persistence and ownership

Decision record 使用 instance-scoped SQLite，在 enqueue 前持久化，保存稳定 identity、workspace/launch/
instance/strategy correlation、candidate/context/Profile/model/tool hashes、状态、时延、tool evidence、policy
结果、effective action hash 与 delivery certainty。restart 将能证明未发送的 nonterminal run 标记 interrupted；
submitting run 标记 indeterminate，均不自动重试。

Execution 通过自己的 typed admission model 和 migration 保存 original/effective canonical Intent、hash、
decision/command/idempotency identity、outcome 与 admission result。original/effective 是同一次 submission；只有
effective Intent 创建 lifecycle state、plan、reservation 和事件。reject/abstain/failure 不创建 Execution
Intent 或 admission audit。Execution/Risk 不依赖 Agent SDK 或 Agent private types。

### Deterministic backtest and event delivery

backtest fixture 必须精确匹配 candidate、context snapshot、Profile、mode、runtime/model/tool profile hashes；
缺失或不匹配直接失败，不回退远程调用。fixture candidate 与 AgentEvent 使用 source event time。

每次 Strategy callback 后、同一 market event 的模拟 fill 前，以及 `on_end` 后、report 生成前，runtime 都等待
fixture worker idle，并由 Strategy dispatch thread 串行派发 `on_agent`。生产事件同样通过 bounded、best-effort
Agent event stream 进入 Strategy ingress，worker 不直接调用 Strategy object。

## Consequences

- 不配置 Agent 时，现有 Execution 行为和依赖保持不变，也不 import optional SDK。
- 每个 Strategy 可用命令式 context 和受授权 mode 表达自己的思路，但不能扩大 Launch 权限。
- Strategy 不承担 Agent lifecycle，也不会形成等待模型结果的第二套 Intent API。
- Execution/Risk/Account/Market 的业务事实所有权不变；模型不能越过 owner boundary。
- gate/revise 增加异步 admission latency和运维面，需要观察 queue、error rate、tool availability、latency 与
  submission certainty；live promotion 必须先收集 shadow evidence 并支持回退 shadow。
- 第一版有意不提供通用 action registry、write MCP、durable distributed workflow 或多 Agent orchestration。
  只有出现跨 restart 自动继续、人工审批、跨模块多阶段动作或横向 worker 的实际需求后，才重新决策。

## Implementation anchors

- Strategy Agent API/runtime：`kairospy/strategy/apps/agent/`
- Strategy dispatch/composition：`kairospy/strategy/apps/runtime/` 与
  `kairospy/strategy/composition/`
- Launch normalized snapshots：`kairospy/system/apps/launch/application/`
- Execution admission boundary：`crates/modules/execution/`
- Python behavior tests：`tests/test_agent_*.py`、`tests/test_strategy_ingress.py`、`tests/test_strategy_host.py`
