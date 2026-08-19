# Strategy Agent Runtime 与受治理业务动作

## 0. 文档状态

- 状态：Proposed（本文是规范，不代表已全部实现）
- 更新日期：2026-08-19
- 第一调用者：Python Strategy runtime
- 第一能力：Execution Intent review
- 第一运行时：OpenAI Agents SDK for Python
- 第一运行方式：Strategy process 内的独立后台 worker

本文定义一个 Strategy-scoped Agent runtime，而不是只为 Intent 设计的小型
`IntentController`。它为每个 Strategy 提供独立 Profile、上下文投影、模式和工具权限；第一个落地能力是
Intent review。Agent 可以提出受限修订，但它不是新的业务事实 owner，也不能绕过
Market、Account、Risk 或 Execution。

公共入口遵循现有 Strategy SDK 的对称模型：

```text
ctx.agent       Strategy 主动发布上下文、撤回上下文、切换已授权模式
on_agent        Strategy 被动观察 Agent 终态事件，不参与审批或提交
ctx.execution   Strategy 提交现有 typed Intent
on_execution    Strategy 观察唯一权威的 Intent/plan/order/fill 业务事实
```

## 1. 背景与目标

当前 Strategy 调用 `ctx.execution.*` 后，typed command 会直接进入 Execution。需要增加一个可选控制环节：

```text
Strategy typed Intent
  -> Decision Agent review
  -> approve / reject / revise / abstain
  -> local deterministic policy
  -> Execution validation and planning
  -> Risk authorization
```

第一版必须满足：

1. Agent 由 Launch 显式启用；关闭时现有行为完全不变。
2. Launch 固定 Profile、model、tools、failure policy、初始模式和 Strategy 可选模式范围。
3. Strategy 通过 `ctx.agent` 发布/撤回私有上下文，并在授权范围内动态切换模式。
4. Strategy 可通过 `on_agent` 观察最小终态事件，但不接收 patch/有效 Intent，不查询或管理 Decision lifecycle。
5. Agent 批准或修订后直接提交 Execution，不等待 `on_agent` 返回；Strategy 通过 `on_execution`
   观察业务事实。
6. Strategy 同步 callback 不执行 model、MCP 或 network I/O。
7. candidate admission 固定 immutable context snapshot、mode 和 mode revision。
8. worker 是 queue、in-flight run 和 SDK resource 的唯一 mutable owner。
9. 模型只返回严格 typed result，不能直接调用业务写接口。
10. revise 只能应用本地 allowlist 中风险单调的 typed revision。
11. 最终 Intent 重新经过 Execution validation 和 Risk authorization。
12. Execution 保存 original/effective Intent 审计；只有 effective Intent 进入生命周期。
13. backtest 不调用远程模型，使用确定性 fixture。
14. credential 不进入 Strategy params、prompt、MCP payload、normalized config、record 或普通日志。

未来可以增加 Market subscription、Risk tightening 或 pause proposal，但第一版不创建通用 action registry、
write MCP tool 或跨业务模块万能协议。

## 2. 非目标

- 通用多 Agent 平台；
- 独立 Agent process 或 durable distributed workflow；
- Agent 自由替换完整 Intent；
- Agent 替代 Risk authorization；
- Strategy 读取模型原始输出、revision patch 或 effective Intent；
- 要求 Strategy 对 Agent 结果进行 acknowledge/二次审批；
- 自动恢复退出前未完成的 live run；
- 自研 model/tool loop、session、trace 或 MCP lifecycle；
- 为未来 Subscription/Risk 动作增加空 API。

## 3. 术语

- **Candidate**：尚未提交给 Execution 的 typed Intent request。
- **Decision run**：具有稳定 ID、snapshot、deadline、Profile、tool evidence 和 terminal status 的一次推理。
- **Proposal**：模型提出、尚未执行的 typed revision。
- **Effective Intent**：approve 的 original，或 revise 后本地重建的 request。
- **Admission evidence**：Execution-owned original/effective Intent 和 correlation 审计。

## 4. 所有权

| 对象 | Owner | 责任 |
|---|---|---|
| signal、假设、策略私有状态 | Strategy | 选择要发布的 Agent context |
| Profile、静态权限、初始/可选模式 | Launch | 固定版本、hash 和安全边界 |
| Agent/MCP/credential resource | Workspace | 资源定义和 secret 解析 |
| 当前 mode、mode revision、context projection | Agent Application | Strategy dispatch thread 内更新 |
| queue、in-flight run、SDK/MCP session | Agent worker | 单一 mutable owner |
| Decision result、proposal、tool evidence | Agent runtime | 内部审计，不是业务事实 |
| Agent terminal notification | Strategy Application | 投递 `on_agent`；不携带 Intent 内容，不影响提交 |
| admission audit、Intent、plan、order、fill | Execution | effective Intent 是唯一 lifecycle truth |
| budget、reservation、authorization | Risk | Agent 不得替代 |
| balance、position、equity、freshness | Account | Agent 只读 |
| observation、order book、subscription | Market | Agent 只读；未来只能 propose |

## 5. 进程与依赖边界

第一版运行在 Strategy process：

```text
StrategyApplication
  StrategyContext.agent: AgentApplication
  Strategy.on_agent(ctx, AgentEvent)
  existing StrategyContext.execution API
  Agent-controlled concrete Execution command adapter
    -> bounded AgentDecisionWorker
       -> OpenAI Agents SDK
       -> approved read-only MCP/local tools
       -> deterministic revision policy
       -> existing Execution command client
```

禁止依赖：

```text
Execution/Risk -> Decision Agent
Strategy -> SDK Runner or worker
Model -> raw UDS/HTTP/provider client
MCP tool -> mutate Execution/Risk/Market/Account
Agent -> write owner snapshots
```

不新增 Kairos-owned `AgentRuntime` trait。当前只有一个生产 runtime；fixture/fake 不构成公共 provider 抽象。

## 6. Strategy 公共入口

Strategy 使用一个命令入口和一个事件入口：

- `ctx.agent`：同步、本地、命令式控制；
- `on_agent(ctx, event)`：异步终态通知，与 `on_market` / `on_execution` 保持一致。

`StrategyContext` 增加 `agent: AgentApplication`。`AgentApplication` 只暴露三个同步命令：

```python
class AgentApplication:
    def publish_context(
        self, document: AgentContextDocument
    ) -> AgentContextReceipt: ...

    def remove_context(self, key: str) -> AgentContextReceipt: ...

    def set_mode(self, mode: AgentMode) -> AgentModeReceipt: ...
```

不暴露 `submit_intent_candidate`、`decision`、`recent_decisions`、`health`、Runner、session 或 tool API。Intent
candidate 仍由 `ctx.execution.*` 的 concrete adapter 内部产生，Strategy 不需要学习第二套提交 API。

### 6.1 `on_agent`

```python
@dataclass(frozen=True, slots=True)
class AgentEvent:
    decision_id: str
    capability: Literal["intent_review"]
    status: Literal[
        "approved", "revised", "rejected", "abstained", "failed", "interrupted"
    ]
    reason_codes: tuple[str, ...]
    occurred_at: datetime

class Strategy:
    def on_agent(self, ctx: StrategyContext, event: AgentEvent) -> None: ...
```

`on_agent` 是 best-effort 观察入口，不是决策回执、人工审批或可靠消费协议：

- Agent 不等待 callback；approve/revise 在本地 policy 通过后立即提交 Execution；
- event 不包含 original/effective Intent、revision、prompt、tool payload 或模型原始输出；
- approved/revised 后的权威业务结果仍从 `on_execution` 到达；
- rejected/abstained/failed 因不会创建 Execution Intent，通过 `on_agent` 提供最小可观察性；
- callback 返回值必须是 `None`，失败按 Strategy callback 的现有错误边界处理，不回滚 Agent/Execution。

### 6.2 Context

```python
@dataclass(frozen=True, slots=True)
class AgentContextDocument:
    key: str
    scopes: tuple[str, ...]
    values: Mapping[str, JsonValue]
    observed_at: datetime | None = None
    expires_at: datetime | None = None
    source_event_sequence: int | None = None
```

- publish 按 key 完整替换；不提供 field patch；
- publish/remove 不触发模型或业务写；
- event sequence/time 从当前 Strategy callback 自动绑定；
- key、scope、字段数、嵌套、字符串和总字节数有硬限制；
- credential-like 字段必须拒绝；
- context projection 是进程内状态，restart 后由 `on_start` 重新发布；
- candidate 只读取匹配 scope、未过期的 immutable snapshot；
- required context 缺失按 capability failure policy，不让模型猜测。

`values` 是显式模型输入/诊断边界，可使用 bounded JSON-compatible value，但不是业务模块 typed adapter。

### 6.3 动态模式

```python
AgentMode = Literal["shadow", "gate", "revise"]

@dataclass(frozen=True, slots=True)
class AgentModeReceipt:
    requested_mode: AgentMode
    effective_mode: AgentMode
    revision: int
    status: Literal["accepted", "duplicate", "rejected"]
    reason: str | None = None
```

Launch 固定 `initial_mode` 和 `strategy_selectable_modes`。`set_mode` 只能选择 allowlist 中的模式；成功切换
递增 `mode_revision`，只影响之后 admission 的 candidate，不能追溯修改 queued/in-flight run。

`disabled` 不是 Strategy mode；它表示不构造 runtime，只能由 Launch 控制。Strategy 不能动态修改 Profile、
model、tools、required、failure policy 或 credential。同一 callback 中先 publish/set_mode 再提交 Intent，
candidate 必须确定读取新 revision。

## 7. Candidate、Result 与 revision

```python
@dataclass(frozen=True, slots=True)
class IntentCandidate:
    decision_id: str
    request_id: str
    intent_id: str
    strategy_id: str
    launch_id: str
    instance_id: str
    operation: str
    request: IntentRequest
    exposure_effect: Literal["increase", "reduce", "neutral", "unknown"]
    mode: AgentMode
    mode_revision: int
    context_watermark: int
    context_snapshot_hash: str
    submitted_at: datetime
    deadline: datetime

@dataclass(frozen=True, slots=True)
class DecisionResult:
    decision: Literal["approve", "reject", "revise", "abstain"]
    confidence_bps: int
    reason_codes: tuple[str, ...]
    risk_flags: tuple[str, ...]
    summary: str
    revisions: tuple[IntentRevision, ...] = ()
```

request 直接使用现有 typed request union，不通过 JSON roundtrip 做内部适配。`decision_id`、`request_id` 和
reserved `intent_id` 在 admission 时稳定生成并 dedupe。exposure effect 由权威 Account projection 和
deterministic helper 计算；无法证明时为 `unknown`。

DecisionResult 约束：confidence 为 0..10000；reason/risk code 使用 Profile allowlist；summary 有长度上限；
approve/reject/abstain 不得带 revision；revise 至少一个；refusal/schema/timeout/MCP error 映射为
abstain/failure，不能伪装 reject。

允许的封闭 revision union：

```python
IntentRevision = (
    ReduceTargetQuantity
    | TightenLimitPrice
    | ShortenDeadline
    | TightenMaxSlippage
    | TightenSplitPolicy
    | RequireMakerExecution
)
```

不接受 RFC 6902 JSON Patch 或完整替换 Intent。revision 原子应用且必须风险单调；不得修改 identity、account、
instrument、route、direction、intent type、leg identity 或 source identity，不得扩大 quantity、max loss、
slippage、deadline 或执行激进程度。

## 8. 运行流程

### 8.1 Gate/Revise

```text
1. Strategy calls existing typed Execution API
2. concrete adapter performs local scope/safety checks
3. adapter builds candidate and immutable context/mode snapshot
4. Decision record is persisted before enqueue
5. callback receives IntentReceipt(PENDING, NOT_SENT)
6. worker runs bounded Agent SDK loop with allowed read tools
7. host validates result and atomically applies allowed revisions
8. host builds effective typed request
9. host calls existing Execution command client with stable identity
10. Execution records admission evidence and validates effective Intent
11. Risk authorizes; Execution continues normal lifecycle
12. Decision record stores downstream result and becomes terminal
```

callback 不等待模型。Strategy 不接收后续 Agent completion。reject 时步骤 9 不发生，Execution 不创建 Intent
或 admission audit。final command 已开始但 delivery certainty 未知时，Decision 标记
`submission_indeterminate`，不得自动重试。

### 8.2 Shadow

Shadow 下 original request 沿现有路径立即提交，然后 candidate 旁路入队。Agent 只保存“如果受控会怎样”的
结果，不得修改或重复提交已发生的 Intent。provider/tool failure 不影响原提交。Shadow Decision 与真实
downstream result 关联，但不是 Execution admission decision。

### 8.3 Disabled

`enabled = false` 时不 import/构造 SDK、worker、MCP、Decision store 或 queue；Execution 使用当前 direct
command client。`ctx.agent` 使用 disabled application，context 命令仍是本地操作，mode 切换返回 rejected。

## 9. Mode 与失败策略

### 9.1 Shadow

- original 立即提交；
- Agent 只产生对照结果；
- paper/live 首次启用必须从 shadow 开始。

### 9.2 Gate

- approve 提交 original；
- reject 不提交；
- revise 视为 abstain，不偷偷应用；
- abstain/failure 按 exposure-aware policy。

### 9.3 Revise

- approve 提交 original；
- reject 不提交；
- revise 经本地 policy 后提交 rebuilt effective request；
- 任一 revision invalid，则整份 proposal invalid；
- effective request 重新执行所有 safety、Execution validation 和 Risk authorization。

### 9.4 Exposure-aware failure

| 场景 | Shadow | Gate/Revise 新增或未知敞口 | 明确减仓/关闭/撤单 |
|---|---|---|---|
| queue full | 原路径提交 | fail closed | bypass Agent |
| timeout/rate limit | 原路径提交 | fail closed | bypass Agent |
| invalid output/refusal | 原路径提交 | fail closed | bypass Agent |
| required MCP/context unavailable | 原路径提交 | fail closed | bypass Agent |
| optional MCP unavailable | 继续并记录 | Profile policy | bypass Agent |
| invalid revision | 不影响原提交 | fail closed | 不适用 |

明确降低绝对敞口的 target、close position、reduce-only order、cancel、强平、补偿和 reconciliation 不得因 Agent
不可用而阻塞。不能仅凭方法名或模型理由判断减仓；必须使用权威 Account projection，无法证明时为 unknown。

## 10. Execution admission audit

Agent 类型不得泄漏到 Execution。Execution 拥有准入模型：

```python
@dataclass(frozen=True, slots=True)
class IntentAdmissionEvidence:
    source: Literal["decision_agent"]
    decision_id: str
    outcome: Literal["approved", "revised"]
    original_intent: ExecutionIntentCommand
    effective_intent: ExecutionIntentCommand
    original_hash: str
    effective_hash: str
```

规则：

- original/effective 属于同一次 submission，不是两个 Intent；
- 只有 effective 创建 `IntentState`、active view、plan、reservation 和 lifecycle event；
- approve 未修改时两份 canonical Intent 可以相同；
- reject/abstain/failure 没有 Execution audit；
- event 可携带 decision ID 和 hashes，但不广播 Strategy context、prompt 或 tool trace；
- evidence 与 Intent admission result 位于同一 Execution persistence boundary；
- idempotency 使用 candidate 的 stable command identity；
- control JSON 必须先 typed decode/validate，不得用 `serde_json::Value` roundtrip 代替 typed mapping。

Execution migration 增加 owner-side `intent_admission_audit`，至少存储 intent/command/idempotency/decision ID、
source、outcome、original/effective canonical Intent、hash、admission result 和 created time。

## 11. Agent runtime 与 worker

第一版使用 optional `openai-agents` dependency。SDK 负责 model/tool loop、structured output、MCP session、
guardrail、run limit 和 trace span。Kairos 负责 candidate admission、snapshot、permissions、Decision schema、
revision policy、business command、Decision record 和 delivery certainty。

一个 worker 拥有 bounded queue、in-flight runs、dedupe、SDK/MCP sessions、health 和 shutdown。callback 只做
validation、snapshot、persist 和 non-blocking enqueue。queue full 立即按 failure policy 返回，不能阻塞。
默认 `max_concurrency = 1`；只有 profiling 和明确 provider/tool budget 后才能提高。

出现跨 restart 自动继续、长时间人工审批、跨模块多阶段动作、横向 worker 或 mutating tool durable retry 的
真实需求后，再评估 DBOS/Temporal。

## 12. MCP 与 tools

第一版 tool 必须只读、provider-neutral、按 workspace/launch/instance/strategy/account scope 收窄，返回 owner
watermark/sequence/time，限制大小、行数、时间范围、latency 和次数，不返回 credential、raw provider payload
或文件内容，不接受 raw SQL、shell、任意 URL 或 unrestricted path。

按 Profile 所需选择首批工具：

```text
reference.get_instrument
market.get_latest_quote
market.get_recent_bars
market.get_freshness
account.get_position
account.get_equity
account.get_available_margin
risk.get_effective_limits
execution.get_active_intents
execution.get_recent_failures
```

禁止 `execution.submit_*`、order replace、risk mutation、market subscribe/release、workspace write、shell、
filesystem write、raw SQL 和 provider request。未来写动作也只能是 `propose_*`，由本地 policy 和 owner
Application 执行。

## 13. Profile 与 Launch 配置

Strategy-specific Profile 位于 Workspace 的
`config/agents/profiles/<profile-id>.toml`。Launch 创建 normalized config 时读取并固化完整快照与 SHA-256；
Strategy 进程使用快照，后续修改源文件只影响下一次 Launch：

```toml
[profile]
id = "mean-reversion-intent-review-v1"
version = "1"
goal = "审查均值回归策略产生的 Intent，避免在信号失效时扩大敞口。"
rubric = [
  "使用 Strategy 发布的 signal 与 regime context",
  "优先降低集中度与价格追逐风险",
]
invalidation_rules = [
  "required context 缺失或过期时 abstain",
  "工具证据冲突时 abstain",
]
reason_codes = ["signal_valid", "signal_stale", "risk_too_high"]
risk_flags = ["concentration", "stale_context", "price_chasing"]
```

```toml
[agent]
enabled = true
required = false
runtime = "openai-agents"
profile = "mean-reversion-intent-review-v1"
max_queue_size = 128
shutdown_timeout_seconds = 5

[agent.model]
provider = "openai"
model = "pinned-model-snapshot"
credential = "openai-agent"
request_timeout_seconds = 5
max_turns = 6
max_tool_calls = 8
max_input_tokens = 32000
max_output_tokens = 2000

[agent.capabilities.intent_review]
initial_mode = "shadow"
strategy_selectable_modes = ["shadow", "gate"]
operations = ["target_position", "pair_arbitrage", "option_spread"]
failure_policy = "reject_new_exposure"
max_decision_age_seconds = 10
required_contexts = ["signal", "regime"]

[agent.capabilities.intent_review.revisions]
allow_quantity_reduction = true
max_price_adjustment_bps = 20
allow_deadline_reduction = true
allow_slippage_reduction = true
allow_split_tightening = true
allow_require_maker = true

[[agent.mcp]]
server = "kairos-context"
profile = "intent-review-readonly"
required = true
```

Normalized config 固化 Profile name/version/hash、goal/rubric/invalidation/reason codes、initial/selectable modes、
operation scope、failure policy、pinned model、limits、MCP logical profile 和 revision policy。Strategy 不得替换
Profile。空 selectable list 表示运行期不能切换。restart 先恢复 initial mode，Strategy 可在 `on_start` 重新
选择已授权 mode。

Launch 只保存 credential reference；Workspace resolver 在 composition 解析 secret。secret 禁止进入 params、
normalized config、prompt/context/tool result、record、trace attribute、exception 或 repr。

## 14. Decision 持久化

第一版使用 instance-scoped SQLite，由单 worker 写。Record 至少包含：

```text
decision/request/reserved intent ID
workspace/launch/instance/strategy
capability/mode/mode revision
candidate type/hash and exposure effect
source/context watermarks and context snapshot hash
Profile/model/tool-profile identities
started/completed/latency
tool argument/result hashes, freshness and status
structured result and local policy outcome
final action hash
Execution result and delivery certainty
terminal status
```

完整 prompt 和长 tool payload 默认不进业务 record。状态至少包括 pending、running、approved、revised、
rejected、abstained、failed、interrupted、submission_indeterminate。启动时把能证明未发送的遗留 nonterminal
gate/revise run 标记 interrupted/not_sent；已经开始 final command 的 record 不自动重试。

## 15. Backtest

backtest 不构造 remote model/MCP client，使用匹配 candidate hash、context snapshot hash、Profile hash、mode、
DecisionResult 和 tool evidence summary 的 fixture。缺失或不匹配 deterministically fail，不回退远程调用。
回放仍执行本地 schema、revision policy、Execution validation 和 Risk logic。

## 16. Health、日志与 shutdown

Launch/System diagnostics 可以读取内部 health：enabled/required/state、mode/revision、context watermark/count、
queue depth/capacity、in-flight、last success/failure、model、MCP/store readiness、rolling error rate 和 latency。
health 不是 Strategy API。

日志关联 workspace/launch/instance/strategy、decision/request/intent、capability/mode/revision、model/tool、outcome、
failure class 和 latency，不记录 secret、完整 prompt、账户 snapshot 或未脱敏 tool payload。

shutdown 顺序：停止 admission；有界取消 shadow；未发送的 gate/revise 标记 interrupted/not_sent；已开始 final
command 的 run 等待 result/certainty；关闭 MCP/SDK/worker/store；超时持久化剩余状态后退出。

## 17. 代码位置

```text
kairospy/application/agent/
  __init__.py
  application.py          Strategy-visible context/mode commands
  models.py               context/mode/candidate/result/receipt/health
  configuration.py        typed normalized Agent config
  composition.py          SDK, MCP, store and adapter assembly
  policy.py               typed revisions and exposure policy
  services/
    worker.py              bounded queue and run owner
    records.py             SQLite Decision records
    openai_runtime.py      thin Agents SDK adapter
    tools.py               local read tools / MCP assembly
    controlled_execution.py concrete Execution command decorator

kairospy/application/strategy/
  composition.py          compose enabled/disabled Agent
  services/context.py     expose ctx.agent and bind event metadata

crates/modules/execution/
  contract/               optional admission evidence in control contract
  src/application/        typed validation
  src/services/           audit persistence
  migrations/             intent_admission_audit
```

不得新增 `ports/`、通用 provider gateway、action registry 或 Agent business fact module。

## 18. 实施阶段

### Phase 0：Runtime spike

- optional dependency；
- structured output、timeout、max turns、trace redaction、shutdown；
- fake candidate/read tool；
- 不接 Execution。

### Phase 1：Context、配置与 Shadow

- public context/mode API；
- normalized config；
- immutable snapshot；
- SQLite store 和 bounded worker；
- shadow adapter、backtest fixture、health/logs。

### Phase 2：Gate

- PENDING/NOT_SENT receipt；
- approve/reject/abstain；
- exposure-aware failure/bypass；
- paper gate 和 fault injection。

### Phase 3：Revise

- typed revision union 和 atomic policy；
- original/effective rebuild；
- Execution evidence contract/persistence；
- paper revise 和 replay。

### Phase 4：Live promotion

- fixed model/Profile/tools；
- shadow evidence 和 latency/cost/error budgets；
- canary gate/revise；
- rollback 到 shadow。

## 19. 测试与验收

### 19.1 API/context/mode

- public API 只有 publish/remove/set_mode；没有 `on_agent`；
- replace/remove/revision/dedupe、scope/TTL/required context；
- bounds 和 credential rejection；
- mode allowlist/revision；
- command 不触发 model/tool；
- queued/in-flight snapshot 不受后续更新影响。

### 19.2 Worker/runtime

- persist-before-enqueue、queue full non-blocking、run dedupe；
- timeout/refusal/max-turn/invalid output；
- required/optional MCP failure；
- final command 不被 SDK retry 包围；
- indeterminate 不自动 retry；
- shutdown/interrupted recovery；
- disabled path 不 import optional SDK。

### 19.3 Policy

- Decision schema bounds；
- identity/account/instrument/route/direction/type 不可修改；
- quantity/loss/slippage/deadline 不扩大；
- multi-revision 原子性；
- exposure classification 和 close/reduce/cancel bypass；
- stale evidence/deadline、reason/tool allowlist。

### 19.4 Execution

- audit 同时保存 original/effective；
- 只有 effective 进入 lifecycle；
- approve unchanged 可相同；
- Agent reject 不创建 Intent/audit；
- admission idempotency；
- typed decode，无 JSON business roundtrip；
- Execution/Risk 不依赖 Agent SDK/private types。

### 19.5 Backtest/security/architecture

- backtest 无网络且 fixture mismatch deterministic fail；
- credential 不进 artifact/log/trace；
- MCP scope/freshness/size/time bounds；
- malicious tool content 不能改变 policy/schema；
- live 新增/unknown fail closed，降险动作不因 outage 阻塞；
- Agent 不导入 business private services；
- Strategy callback 不调用 SDK；
- 无 write MCP、action registry 或 provider mirror trait。

### 19.6 仓库验证

```text
uv run pytest -q
ruff check kairospy tests
pyright
cargo test --workspace
cargo fmt --all -- --check
git diff --check
python3 scripts/check/check_crate_layout.py
python3 scripts/check/check_workspace_dependencies.py
```

若全仓检查被无关既有失败阻塞，报告精确失败，并运行最窄相关验证。

## 20. Promotion evidence

从 shadow 提升前收集 candidate/result 数量、queue/timeout/rate-limit/MCP/schema errors、p50/p95/p99 latency、
token/tool/cost、shadow 与真实结果差异、revision rejection、人工标注的 false reject/unsafe approve、outage 和
rollback 演练。阈值由首个生产 Strategy 与风险预算确定。

## 21. 完成清单

- [x] 公共 API 已实现并导出；
- [x] Launch Agent config 已校验、规范化并装配；
- [x] disabled 与现有行为一致且不加载 optional SDK；
- [x] context/mode projection 只有一个 mutable owner；
- [x] candidate 固定 immutable context/mode snapshot；
- [x] Decision store persist-before-enqueue、dedupe、terminal 语义完成；
- [x] OpenAI Agents SDK structured output 与只读 MCP 完成；
- [x] shadow/gate/revise 完成；
- [x] exposure-aware failure/bypass 完成；
- [x] typed revision 与风险单调 policy 完成；
- [x] Execution original/effective admission audit 完成；
- [x] 只有 effective Intent 进入 lifecycle；
- [x] backtest fixture 无网络且确定性重放；
- [x] health/log/redaction/shutdown 完成；
- [x] behavior/failure/security/architecture 测试完成；
- [ ] 全仓验证完成；
- [x] 未提前实现未来 Subscription/Risk 通用动作；
- [x] 未改变现有业务事实 owner。
