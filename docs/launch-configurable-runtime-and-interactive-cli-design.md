# Launch 可配置运行时与交互式 CLI Strategy 设计

## 1. 背景与结论

当前 Kairos 是多进程架构：一个 launch instance 负责组装和管理 Account、
Risk、Execution、Market 以及 Strategy。现在 `launch start` 要求 Strategy
必须存在，且 Account、Execution 的启动基本由固定流程决定，因此用户在不
编写策略对象时，无法用同一套 launch 机制完成账户观察或人工操作。

旧版本的 `CliStrategyBase` 已经证明了人工操作应当经过 Strategy Context，
而不是绕过 Account、Risk 和 Execution。当前版本进一步收敛为一个交互式
Python 入口：用户提交的代码由 Strategy 的 `on_command` 生命周期入口执行。

本设计将这个能力迁移到当前架构，但不引入 `system session`。所有运行时都
属于普通 launch；launch 只是在用户策略和 Kairos 内置
`InteractiveStrategy` 之间选择一个 Strategy。

## 2. 目标与非目标

目标：

- 一个 launch 可以配置零个、一个或多个 Account。
- 每个 Account 可以单独启用或禁用。
- Execution 可以启用或禁用。
- Strategy 始终存在，但可以是用户策略或 `builtin:interactive`。
- `launch attach --python` 提供 Python 交互入口。
- 交互命令仍经过 Strategy、Account、Risk 和 Execution 边界。
- 缺少某项能力时，只让依赖该能力的命令失败，不让整个 launch 无法启动。
- 现有 launch 配置保持向后兼容。

非目标：

- 不提供固定 CLI 命令映射作为第二套交互协议。
- 不建立绕过 Risk/Execution 的人工下单通道。
- 不新增全局 system runtime 或第二套生命周期管理器。

## 3. 运行时模型

```text
Launch instance
  ├── 0..N Account processes
  ├── optional Execution process
  ├── optional Market process
  └── exactly one Strategy process
        ├── user strategy: module:callable
        └── builtin strategy: builtin:interactive
```

Account、Execution 和 Strategy 都是 instance-owned process，并通过 instance
 manifest 发现彼此的 Unix socket 和 health 文件。`InteractiveStrategy` 不是特殊
launch 类型，而是一个实现标准 Strategy contract 的内置策略。

## 4. Launch 配置

### 4.1 Strategy 选择

```toml
[launch]
id = "manual-paper"
mode = "paper"
strategy = "builtin:interactive"
```

用户策略继续使用：

```toml
[launch]
id = "btc-sma"
mode = "paper"
strategy = "strategies.sma:Strategy"
```

Strategy loader 支持两种引用：`module:callable` 和 `builtin:interactive`。
`on_command` 是所有 Strategy 都可以选择实现的扩展入口，不要求普通策略继承
`InteractiveStrategy`。

### 4.2 多账户

```toml
[accounts.main]
ref = "main"
enabled = true

[accounts.secondary]
ref = "secondary"
enabled = true
```

规则：

- `main`、`secondary` 是 launch 内的逻辑名称。
- `ref` 指向 workspace-owned account binding。
- `enabled = false` 的账户不启动、不申请 lease，也不写入 manifest。
- `[accounts]` 不存在或没有启用项时，launch 不启动 Account process。

当前单账户形式继续支持：

```toml
[account]
ref = "main"
enabled = true
```

内部规范化后，单账户形式和 `[accounts.main]` 使用同一个模型。若两种形式
同时出现并重复引用同一个 `ref`，启用状态不一致时配置校验失败。

### 4.3 Execution

```toml
[execution]
enabled = true
provider = "simulated"
product = "spot"
```

规则：

- `enabled = false` 时不启动 Execution，也不写入 execution endpoint。
- 未配置 `enabled` 时默认为 `true`，保持现有行为。
- provider、product、routes 等已有字段继续有效。
- 没有 Execution 时，账户查询和 Market 订阅仍可工作。
- 没有 Execution 时，目标仓位命令返回 capability-disabled 错误。

### 4.4 示例

账户观察：

```toml
[launch]
id = "account-monitor"
mode = "paper"
strategy = "builtin:interactive"

[accounts.main]
ref = "main"
enabled = true

[accounts.secondary]
ref = "secondary"
enabled = true

[execution]
enabled = false
```

用户策略和行情研究：

```toml
[launch]
id = "market-research"
mode = "paper"
strategy = "research.strategy:ResearchStrategy"

[execution]
enabled = false
```

人工操作：

```toml
[launch]
id = "manual-trading"
mode = "paper"
strategy = "builtin:interactive"

[accounts.main]
ref = "main"
enabled = true

[execution]
enabled = true
provider = "simulated"
product = "spot"
```

## 5. Launch 启动流程

```text
load and validate launch.toml
  -> resolve enabled account refs
  -> acquire leases for enabled accounts
  -> prepare instance workspace
  -> start required Market resources
  -> start enabled Account processes
  -> start Risk boundary
  -> write manifest with available endpoints
  -> start Execution when enabled
  -> refresh manifest
  -> start selected Strategy
  -> enable Strategy
```

Account 只为启用项调用 `ensure_running("account", ...)`。每个账户拥有独立的
逻辑名称和 endpoint，例如：

```json
{
  "accounts": {
    "main": {"socket": ".../main.sock"},
    "secondary": {"socket": ".../secondary.sock"}
  }
}
```

没有启用账户时，manifest 的 `accounts` 是空对象，而不是伪造一个 endpoint。
没有 Execution 时，manifest 不包含 `components.execution`。Strategy composition
必须能够识别可选 endpoint，不能直接索引一个必然存在的 execution socket。

## 6. InteractiveStrategy 与 `on_command`

### 6.1 Strategy contract

当前 Strategy lifecycle 增加一个请求/响应型的可选 `on_command` 入口：

```python
class StrategyProtocol(Protocol):
    async def on_command(
        self,
        context: StrategyContextProtocol,
        command: CommandEnvelope,
    ) -> CommandResult:
        ...
```

`on_data`、`on_clock` 等生命周期回调仍然是事件通知；`on_command` 是外部
控制请求，因此需要返回结构化结果：

```python
@dataclass(frozen=True)
class CommandEnvelope:
    request_id: str
    kind: str
    source: str
    issued_at: datetime

@dataclass(frozen=True)
class CommandResult:
    request_id: str
    status: str
    result: Mapping[str, object] = {}
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
```

普通用户策略也可以实现 `on_command`，但需要自己定义并校验命令协议。例如，
策略可以只接受自己的结构化命令：

```python
async def on_command(self, context, command):
    if command.kind != "strategy.rebalance":
        return CommandResult.rejected(command.request_id, "unsupported command")
    target = self.parse_rebalance(command.payload)
    return await self.rebalance(context, target)
```

普通策略不应默认执行任意 source；是否支持 `interactive.python`、允许哪些
namespace 以及如何校验输入，都由策略自己负责。

`InteractiveStrategy` 是 Kairos 提供的默认 Python 交互实现。它在 `on_command`
中对 `kind = "interactive.python"` 的 source 进行异步执行，并向 namespace 注入：

```python
{
    "strategy": strategy,
    "context": context,
    "accounts": context.accounts,
    "execution": context.execution,
    "market": context.market,
    "launch": context.launch,
}
```

当前 Strategy Context 需要增加 Account capability，并复用
`kairospy.infrastructure.contracts.account.AccountContractClient`。账户查询
只能选择当前 launch manifest 中启用的账户：

```python
context.accounts.current(account="main")
context.accounts.balance("USDT", account="main")
context.accounts.position("BTCUSDT", account="secondary")
```

用户代码通过 Context 操作账户和执行能力；目标仓位继续使用现有 intent command
port，保持 Strategy -> Risk -> Execution 链路。`InteractiveStrategy` 不直接
调用 Execution Server。

`on_command` 的 dispatch 规则如下：

1. Control Server 只负责解码 `CommandEnvelope`，不解释 command kind。
2. Strategy Host 将 command 串行交给当前 Strategy 的 `on_command`。
3. Strategy 自己决定支持哪些 kind、如何授权和如何返回结果。
4. Strategy 没有实现 `on_command` 时，返回稳定的 unsupported-command 结果，
   不使进程失败。
5. `builtin:interactive` 默认只支持 `interactive.python`。

当 capability 不存在时返回明确错误，例如：

```text
account 'main' is not enabled for this launch
execution is disabled for this launch
```

`InteractiveStrategy` 不应因为没有 Market snapshot 才能启动；它是一个不要求
行情输入的交互 Strategy。用户策略是否需要 Market、Account 或 Execution，
由其实际调用决定。

## 7. `launch attach --python`

当前 `launch attach` 只读取状态和 strategy 日志。增加交互模式：

```bash
kairos launch attach manual-trading --python
```

不带 `--python` 时保持现有非交互输出，方便脚本和观测工具使用。

attach 进程负责终端 IO，但 Python source 在 Strategy process 内执行。attach
只负责把输入代码发送到 Strategy instance control socket，并显示结果：

```python
>>> positions = await accounts.positions("main")
>>> [p for p in positions if p.instrument == "BTCUSDT"]
>>> context.target_position("BTCUSDT", "0.1", account="main")
```

这里的 Python 交互不是固定命令映射，而是用户对当前 Strategy Context 的
临时编程。代码能够访问当前 Strategy 的真实对象和状态，但只适用于 workspace
owner 信任的本地环境，不提供多租户 Python 沙箱。

### 7.1 Strategy control 接口

Strategy Control Server 增加：

```text
POST /v1/command
```

请求：

```json
{
  "request_id": "interactive:instance-1:1",
  "kind": "interactive.python",
  "source": "await accounts.current('main')"
}
```

响应：

```json
{
  "request_id": "interactive:instance-1:1",
  "status": "completed",
  "result": {},
  "stdout": "",
  "stderr": "",
  "error": null,
  "duration_ms": 12
}
```

控制接口必须复用 launch instance socket，不能新增 workspace-global socket，以
确保代码绑定到正确的 launch、instance 和 Strategy identity。

### 7.2 事件循环和执行语义

交互代码不能通过 `input()` 嵌入 Strategy event loop。Strategy Host 同时管理：

```text
Strategy event loop
  ├── Market event stream
  ├── Strategy callbacks
  ├── Control server
  └── Interactive command queue
```

Interactive command 进入 queue 后与 Strategy callback 串行调度，避免用户代码
和 `on_data` 同时修改策略状态。第一版采用协作式异步执行：

- 支持单行表达式和多行代码块。
- 支持 top-level `await`。
- `on_command` 是异步入口。
- 每个 request 有执行超时。
- 捕获 stdout、stderr 和 traceback。
- 支持 interrupt/cancel。
- `/v1/command/cancel` 可以取消仍在等待中的异步 command。
- 不承诺支持 `time.sleep`、无限循环等阻塞代码。

第一版不引入 worker thread。跨线程执行会引入 Strategy、Context 和用户状态
的同步问题，待有明确需求和测量证据后再考虑。

## 8. 边界与安全约束

1. Python 代码在用户本地 attach 进程执行。
2. 远端只接收结构化命令，不接收 `eval`、`exec` 或任意 Python 源码。
3. 目标仓位继续经过 Risk、trade lease、live safety 和 Execution policy。
4. Account 查询只能访问当前 launch 启用的账户。
5. 禁用能力不能通过手工构造 socket 路径绕过 manifest。
6. 每个 interactive request 记录 launch id、instance id、strategy id、request
   id、代码摘要、耗时和结果状态，但不得记录 credential 或 secret。
7. Python 交互只面向受信任的 workspace owner；不把它定义为安全沙箱。

## 9. 实施计划

### 阶段一：配置和 launch composition

- 扩展 `LaunchConfig`、`LaunchPlan`，支持多账户、enabled 和可选 Execution。
- 只为启用账户申请 lease 和启动 Account。
- 让 manifest 表达可选 endpoint。
- 保持旧配置默认行为。

### 阶段二：可选 Strategy capabilities

- 将 Account contract 接入 `StrategyClientBundle`。
- 增加 `context.accounts`。
- 让 Strategy composition 支持缺失 Account、Execution 和 CLI strategy 的
  无 Market 启动。
- 增加 capability-disabled 错误。

### 阶段三：内置 InteractiveStrategy

- 增加 `on_command` 和 `CommandEnvelope` / `CommandResult`。
- 支持 `builtin:interactive` loader 引用。
- 实现 Strategy namespace 和异步 Python 执行。
- 增加多账户访问、Execution capability 和异常测试。

### 阶段四：Interactive attach

- 增加 Strategy Control Server `/v1/command` 接收 Python source。
- 增加 `launch attach --interactive`。
- 实现本地 terminal IO 和远端 code result 展示。
- 保持默认 attach 的非交互输出不变。

### 阶段五：清理与文档

- 不引入或删除重复的 system session 概念和旧 CLI runtime 路径。
- 更新 README、CLI help 和示例配置。
- 增加 architecture checks，确认 CLI 不直接访问 Strategy 私有实现。

## 10. 验证策略

至少覆盖：

- 零账户 launch 可以启动。
- 多账户 launch 只启动 enabled 账户。
- disabled account 不申请 lease、不出现在 manifest。
- Execution disabled 时 launch 仍可启动。
- Execution disabled 时目标仓位返回 capability error。
- Account disabled 时账户查询返回 capability error。
- `builtin:interactive` 可以被 loader 加载。
- 多账户查询选择正确。
- interactive attach 连接当前 active instance。
- `on_command` 与 Market callback 串行执行。
- interactive request 支持异常、超时、取消和 stdout/stderr 返回。
- 用户策略和 builtin InteractiveStrategy 使用同一套 launch lifecycle。

完成后运行：

```text
cargo test --workspace
uv run pytest -q
cargo fmt --all -- --check
git diff --check
```
