# Interactive Context Command Design

本文定义 Kairos system attach 交互控制台的长期设计：用户可以 attach 到 `kairos-system`，用字符串命令直接执行策略上下文能力，例如订阅行情、查看订阅、读取策略状态。设计目标是让系统策略和用户策略复用同一套命令解析与执行语义。

## 背景

当前 `kairos system attach --shell` 已经具备交互入口，但它只支持少量 attach 本地命令和通用 `command KIND JSON` 转发。系统 runtime 内置策略是 `CliStrategyBase`，它能把 `system/cli.command` 事件转成策略上下文动作。

现有问题：

- `status` 曾被误投递为 runtime command，进入 `CliStrategyBase` 后导致 `unsupported cli strategy command: status`。
- `ControlFactory` / `ControlJournal` 只记录请求，不执行控制动作，已决定移除，避免保留未完成 API。
- 用户希望通过 attach shell 直接订阅行情，并且希望这些命令能以字符串表示，便于系统策略和用户策略复用。

## 目标

最终交互体验：

```text
uv run kairos system attach --shell

kairos/system> status
kairos/system> subscribe BTC/USDT --exchange binance --market spot --quote
kairos/system> subscribe BTC/USDT --exchange binance --market spot --bar 1m
kairos/system> subscriptions
kairos/system> view market.subscriptions
kairos/system> unsubscribe <subscription-key>
```

底层通用命令也必须可用：

```text
kairos/system> command subscribe '{"subject":"BTC/USDT","exchange":"binance","market_type":"spot","selectors":["quote"],"identity":"manual"}'
kairos/system> command subscriptions '{}'
kairos/system> command view '{"key":"market.subscriptions"}'
```

策略内也可以复用同一套字符串命令：

```python
class MyStrategy(CliStrategyBase):
    strategy_id = "my-strategy"

    def on_start(self, context):
        self.execute_context_command(
            context,
            "subscribe BTC/USDT --exchange binance --market spot --quote",
        )
```

## 非目标

- 不恢复 `ControlFactory` / `ControlJournal`。
- 不实现异步 `request_subscription` 控制请求。
- 不把 `status` / `inspect` 等诊断命令送进策略命令通道。
- 不要求第一版支持复杂 DSL、脚本、管道或权限系统。
- 不为旧的 `control` cli command 保留兼容。

## 设计原则

1. **直接执行优先**：交互订阅应直接调用 `context.subscribe(...)` / `context.unsubscribe(...)`。
2. **字符串是公共接口**：attach shell、system command、用户策略都复用同一套字符串解析器。
3. **结构化 payload 是内部协议**：解析后的命令使用结构化对象执行，避免散落字符串判断。
4. **诊断命令本地处理**：`status`、`inspect`、`help`、`exit` 永远由 attach shell 本地处理。
5. **命令错误必须可控**：未知命令返回 rejected/error，不应导致 system runtime failed。
6. **系统策略可继承**：`CliStrategyBase` 是系统策略和用户交互策略的基类。
7. **统一命令内核**：人工 attach 命令和策略内复用命令必须走同一套解析、执行和错误处理路径，方便 debug、复现和测试。
8. **Context 不依赖 CLI 框架**：可以借鉴命令注册/分发模式，但 `Context` 本身不能变成 Typer app。

## 架构

目标链路：

```text
Attach shell input
  -> Attach local command router
  -> ContextCommandParser
  -> system command: cli.command
  -> CliStrategyBase
  -> ContextCommandExecutor
  -> RuntimeContext
  -> MarketRuntime / ViewStore / AccountQueryService
```

用户策略内复用链路：

```text
User strategy method
  -> CliStrategyBase.execute_context_command(context, text)
  -> ContextCommandParser
  -> ContextCommandExecutor
  -> RuntimeContext
```

### 模块建议

新增模块：

```text
kairospy/application/strategy/commands.py
```

职责：

- 定义 `ContextCommand`
- 解析字符串命令
- 解析 JSON payload 命令
- 执行命令到 `Context`
- 提供 selector 解析

### 统一命令内核

这里的重点不是使用 Typer，而是建立一个更纯净的命令内核：人工命令和策略命令都进入同一套 parser、executor 和返回值模型。

目标是避免出现两条行为路径：

```text
attach shell command
  -> 一套解析和执行逻辑

strategy code command
  -> 另一套解析和执行逻辑
```

统一后应变成：

```text
attach shell text
  -> ContextCommandApp
  -> ContextCommandExecutor
  -> Context

strategy string command
  -> ContextCommandApp
  -> ContextCommandExecutor
  -> Context

structured cli.command payload
  -> ContextCommandApp
  -> ContextCommandExecutor
  -> Context
```

这样有几个直接收益：

- attach 中执行过的命令可以原样复制到策略里复用。
- 策略里执行的字符串命令可以原样贴到 attach shell 中 debug。
- 解析错误、执行错误和返回结构一致，测试只需覆盖一套命令内核。
- 每次命令执行都可以统一 trace，便于定位“命令没解析对”还是“context 执行失败”。
- 后续 UI、API 或脚本入口也能复用同一套命令内核。

### CLI 框架边界

不建议把 `Context` 本身做成 Typer app。

`Context` 是策略运行时 API，职责是执行能力：

```python
context.subscribe(...)
context.unsubscribe(...)
context.require_view(...)
context.target_position(...)
```

Typer 是 CLI 参数解析和进程入口框架。若让 `Context` 直接依赖 Typer，会让 runtime/strategy 层反向依赖 surface/CLI 技术栈，并带来这些问题：

- Typer/Click 容易通过 `SystemExit` 和 CLI exception 表达错误，不适合 runtime command response。
- Typer 默认面向 stdout/stderr/exit code，而 context 命令需要结构化返回值。
- 策略内部复用命令时不应该依赖 CLI 进程语义。
- 测试 fake context 时，手写 parser/registry 比 Typer app 更轻量。

推荐做法是实现一个 **internal context command app**：

```text
ContextCommandApp
  -> parse text / JSON payload
  -> produce ContextCommand
  -> execute against Context
```

职责边界：

```text
ContextCommandApp
  负责命令注册、参数解析、错误归一化、help 文本

Context
  负责真正执行 runtime/strategy 能力
```

第一版可以用 `shlex` 和明确 parser 实现：

```python
parse_context_command(
    "subscribe BTC/USDT --exchange binance --market spot --quote"
)
```

返回：

```python
ContextCommand(
    name="subscribe",
    args={
        "subject": "BTC/USDT",
        "exchange": "binance",
        "market_type": "spot",
        "selectors": ["quote"],
    },
)
```

如果后续命令增多，再演进为内部 registry：

```python
app = ContextCommandApp()

@app.command("subscribe")
def parse_subscribe(args: Sequence[str]) -> ContextCommand:
    ...

@app.command("view")
def parse_view(args: Sequence[str]) -> ContextCommand:
    ...
```

这可以获得清晰的命令组织方式，但不会让 runtime 层依赖 Typer。

建议类型：

```python
@dataclass(frozen=True, slots=True)
class ContextCommand:
    name: str
    args: Mapping[str, object]
    source: str = "cli"
```

建议入口：

```python
def parse_context_command(text: str) -> ContextCommand:
    ...

def context_command_from_payload(name: str, payload: Mapping[str, object]) -> ContextCommand:
    ...

def execute_context_command(context: Context, command: ContextCommand) -> object:
    ...
```

`CliStrategyBase` 只负责桥接：

```python
class CliStrategyBase(StrategyBase):
    def on_cli_command(self, context, command, signal):
        execute_context_command(context, context_command_from_payload(command.name, command.args))

    def execute_context_command(self, context, text: str) -> object:
        return execute_context_command(context, parse_context_command(text))
```

## 命令集

### `subscribe`

字符串形式：

```text
subscribe BTC/USDT --exchange binance --market spot --quote
subscribe BTC/USDT --exchange binance --market spot --bar 1m
subscribe market.ohlcv.binance.spot.btc_usdt.1m --identity my-strategy
```

结构化形式：

```json
{
  "subject": "BTC/USDT",
  "exchange": "binance",
  "market_type": "spot",
  "selectors": ["quote"],
  "identity": "manual"
}
```

执行：

```python
context.subscribe(
    subject,
    selectors=selectors,
    exchange=exchange,
    market_type=market_type,
    identity=identity,
)
```

返回建议：

```json
{
  "subscription": {
    "key": "...",
    "subject": "BTC/USDT",
    "selectors": ["quote"]
  }
}
```

### `unsubscribe`

字符串形式：

```text
unsubscribe <subscription-key>
```

结构化形式：

```json
{"key": "<subscription-key>"}
```

执行：

```python
context.unsubscribe(key)
```

### `subscriptions`

字符串形式：

```text
subscriptions
```

执行：

```python
context.require_view("market.subscriptions")
```

返回 `market.subscriptions` view 的 JSON-safe 表示。

### `view`

字符串形式：

```text
view market.subscriptions
view market.windows
view market.window.binance_spot_btc_usdt.quotes
```

结构化形式：

```json
{"key": "market.subscriptions"}
```

执行：

```python
context.require_view(key)
```

### `account.current`

保留现有语义：

```text
account.current
account.current main
```

执行：

```python
context.accounts.current(account)
```

### `target_position`

保留现有语义，但后续也应支持字符串形式：

```text
target_position BTC/USDT 0.01 --account main --reason manual
```

## Selector 语法

第一版支持：

```text
--quote        -> Quote
--trade        -> TradePrint
--orderbook    -> OrderBookSnapshot
--bar 1m       -> Bar.select(interval="1m")
--rate         -> RateObservation
```

结构化 payload 支持：

```json
{"selectors": ["quote", "bar:1m", "trade", "orderbook", "rate"]}
```

解析失败必须返回明确错误：

```text
unsupported selector: depth
```

## Attach Shell 路由

attach shell 分两类命令。

本地命令，不进 runtime command queue：

```text
help
status
inspect
stop
exit
trade-status
trade-acquire
trade-release
```

上下文命令，转成 `cli.command`：

```text
subscribe ...
unsubscribe ...
subscriptions
view ...
account.current ...
target_position ...
trace ...
```

通用形式仍支持：

```text
command KIND [JSON|--payload-json JSON]
```

但 `command status` 应该被拒绝，并提示使用本地 `status`：

```text
status is an attach command; use `status`, not `command status`
```

## System Command 队列行为

`SystemCommandDispatcher` 继续处理真正的系统控制面命令：

```text
runtime.stop
account.current
account.balances
account.positions
account.open_orders
account.pending_orders
account.trade-status
account.trade-acquire
account.trade-release
order.status
```

非 dispatcher 命令才转成 `cli.command` 给 `CliStrategyBase`。

重要约束：

- `status`、`inspect` 不允许进入队列。
- 未知 context command 不应杀死 runtime。第一版可以在 `CliStrategyBase` catch `ValueError` 后 `context.trace("cli.error", ...)`，后续再把错误写入 command response。

## 返回值与可观测性

`CliStrategyBase` 当前通过 `context.trace(...)` 把查询结果投影到 trace。第一版可沿用这个模式：

```python
result = execute_context_command(...)
context.trace(f"cli.{command.name}", {"result": result})
```

后续如果需要 attach shell 同步拿到返回值，应增强 command response：runtime 处理 `cli.command` 后把执行结果写入 response，而不是只返回 `{"processed": true}`。

分阶段策略：

1. 第一版：执行命令并 trace 结果。
2. 第二版：`cli.command` response 包含执行结果。

## 实施计划

### 阶段 1：命令解析与 `CliStrategyBase` 执行

- 新增 `kairospy/application/strategy/commands.py`
- 实现 `parse_context_command`
- 实现 `execute_context_command`
- `CliStrategyBase` 改为委托该模块
- 支持 `subscribe`、`unsubscribe`、`subscriptions`、`view`

验收：

```text
command subscribe '{"subject":"BTC/USDT",...}' 能调用 context.subscribe
command subscriptions 能读取 market.subscriptions
```

### 阶段 2：attach shell 语法糖

- 在 `parse_attach_shell_command` 中识别 `subscribe` / `unsubscribe` / `subscriptions` / `view`
- 转成 `cli.command`
- 拒绝 `command status` / `command inspect`

验收：

```text
kairos/system> subscribe BTC/USDT --exchange binance --market spot --quote
kairos/system> subscriptions
```

### 阶段 3：同步响应

- 扩展 `_SystemCommandEventLine` 或 runtime session，让 `cli.command` 执行结果能写入 response
- attach shell 显示 command result，而不是只看 trace/log

验收：

```text
command subscriptions
```

直接返回 `market.subscriptions`。

### 阶段 4：用户策略复用

- 文档化用户策略继承 `CliStrategyBase`
- 提供 `execute_context_command(context, text)` 示例
- 确保字符串命令和 JSON payload 命令同源解析

## 测试计划

单元测试：

- `parse_context_command("subscribe BTC/USDT --exchange binance --market spot --quote")`
- `parse_context_command("subscribe BTC/USDT --bar 1m")`
- selector 解析成功/失败
- `execute_context_command(... subscribe ...)` 调用 fake context 的 `subscribe`
- `execute_context_command(... unsubscribe ...)` 调用 fake context 的 `unsubscribe`
- `execute_context_command(... subscriptions ...)` 调用 `require_view("market.subscriptions")`

交互测试：

- attach shell `status` 本地处理，不创建 system command file
- attach shell `subscribe ...` 转成 `cli.command`
- `command status` 被拒绝，不进入 runtime

运行时测试：

- system runtime 收到 `subscribe` 不进入 failed
- 订阅后 `market.subscriptions.active_count` 增加
- `unsubscribe` 后 subscription 消失

## 迁移说明

已决定移除：

```text
ControlFactory
ControlJournal
ControlRequest
ControlRequestKind
context.control
CliStrategyBase control command
system.control view schema
```

后续所有交互订阅都走：

```text
CliStrategyBase -> context.subscribe / context.unsubscribe
```

若未来需要“策略提出请求，runtime 经风控审批后执行”的异步机制，应重新设计为独立能力，不复用这次已移除的半成品 API。

## 开放问题

- 第一版是否允许 system runtime 没有 market data port 时订阅失败并继续运行？建议是返回 rejected，不让 runtime failed。
- `subscriptions` 查询结果是仅 trace，还是必须同步 command response？建议第一版 trace，第二版同步 response。
- 用户策略继承 `CliStrategyBase` 后，未知命令是否应调用用户自定义 hook？建议提供：

```python
def on_unknown_cli_command(self, context, command, signal):
    raise ValueError(...)
```

这样用户策略可以扩展自己的字符串命令。
