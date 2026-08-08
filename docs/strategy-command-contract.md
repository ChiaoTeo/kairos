# Strategy Command Contract

## 1. 目的

Strategy 发出的命令必须能够在 Market、Account、Risk、Execution 之间被可靠地追踪、校验、重试和审计。命令不能只是一段没有版本的 JSON 或一个 Python dataclass。

本规范覆盖两类 Strategy command：

- `market.subscribe` / `market.unsubscribe`
- `execution.submit_intent`

后续 Account → Risk、Account → Execution 的内部 command 也复用同一套 envelope 和 result 语义，但不直接暴露 provider payload。

## 2. 统一 Command Envelope

所有命令都必须携带：

```json
{
  "schema_version": 1,
  "command_id": "strategy-1:market.subscribe:42",
  "idempotency_key": "strategy-1:market.subscribe:42",
  "issued_at_unix_nanos": 0,
  "operation": "market.subscribe",
  "strategy_id": "strategy-1",
  "launch_id": "paper-aapl",
  "instance_id": "run-001",
  "source": {
    "stream_id": "market.events",
    "sequence": 123,
    "snapshot_id": "market:7"
  },
  "payload": {}
}
```

规则：

- `command_id` 是本次命令的稳定身份；重试不能生成新的业务命令。
- `idempotency_key` 用于接收方去重；默认等于 `command_id`，但允许上层在重放时显式指定。
- `strategy_id`、`launch_id`、`instance_id` 是路由和审计字段，不能依赖默认值。
- `source` 可为空，但如果命令由行情/事件回调触发，应携带事件流身份和序号。
- `schema_version` 必须在 transport 层校验；未知版本拒绝，不静默降级。
- `payload` 只允许业务 request，不允许 vendor SDK payload、连接对象或 persistence record。

## 3. 统一 Command Result

```json
{
  "schema_version": 1,
  "command_id": "strategy-1:market.subscribe:42",
  "status": "accepted",
  "accepted_at_unix_nanos": 0,
  "operation_id": "market-subscription:123",
  "result": {},
  "error": null
}
```

`status` 只能使用：

- `accepted`：命令已被业务 owner 接收并产生业务对象；
- `pending`：已接收但尚未完成；
- `completed`：同步或异步业务动作已经完成；
- `rejected`：命令没有进入业务状态变更；
- `duplicate`：同一幂等键已处理，返回第一次处理结果；
- `failed`：已接收但执行过程中失败，需要根据 error 判断是否可重试。

错误必须包含稳定的 `code`、可读的 `message`、`retryable` 和可选 `details`。调用方不能根据错误字符串判断业务分支。

## 4. Market Subscribe Command

```json
{
  "subscription_id": "strategy-1:subscription:btc",
  "instrument": {
    "symbol": "BTCUSDT",
    "venue_id": "binance",
    "market_type": "spot",
    "asset_type": "crypto"
  },
  "selectors": [
    {"kind": "quote"},
    {"kind": "bar", "interval": "1m"}
  ],
  "mode": "static"
}
```

规则：

- 策略声明的是 instrument intent；`market_id` 和 `instrument_id` 由全局 Reference 根据 symbol、venue、market_type、asset_type 解析并返回。
- `asset_type` 是可选的路由维度，但当同一 venue/product 下存在多种资产类型时必须填写，例如 OKX `spot + crypto` 和 `spot + equity`。
- selector 当前仍以受控字符串表示（例如 `quote`、`bar:1m`）；新的命令 envelope 不提供旧格式兼容入口。后续若扩展 selector 语义，应升级 payload schema，而不是增加第二套 envelope。
- `subscription_id` 是策略侧幂等身份；同一策略重复提交相同 subscription 不得创建第二条业务订阅。
- `mode=static` 表示订阅固定 Reference market；动态查询另定义 `market.subscribe_query`，不能用一个 `dynamic` 布尔值掩盖两种语义。

## 5. Execution Strategy Intent Command

```json
{
  "intent_id": "strategy-1:intent:42",
  "kind": "target_position",
  "account_id": "paper-account",
  "segment_key": "spot",
  "instrument": {
    "instrument_id": "instrument:binance:spot:BTCUSDT",
    "market_id": "market:binance:spot:BTCUSDT",
    "venue_id": "binance",
    "market_type": "spot",
    "asset_type": "crypto"
  },
  "target": {"quantity": {"mantissa": 125, "scale": 2}},
  "order_policy": {
    "limit_price": null,
    "time_in_force": "day",
    "reduce_only": false,
    "post_only": false
  },
  "reason": "rebalance"
}
```

规则：

- `intent_id` 必须由 Strategy 或上层 command 稳定生成，不能由 transport 接收后随意替换。
- `account_id` 和 `segment_key` 必须显式传递；不允许使用 `main`、`spot` 等隐式默认值跨越 Account 边界。
- Execution 负责接受、校验、持久化并执行 intent；Account 提供授权、余额和持仓事实；Risk 负责 reservation/budget；Execution 负责把 intent 转换为跨账户订单生命周期。
- `target_position` 与具体订单参数分离；订单策略只表达允许的业务约束，不能携带 Binance/OKX/Massive 的原始字段。
- intent 必须保留创建时的 source event identity，便于 Execution 审计使用了哪一份行情/快照。

## 6. 传输和 owner 边界

```text
Strategy
  -> StrategyCommandBus
      -> MarketApplication      (market.subscribe)
      -> ExecutionApplication     (execution.submit_intent)
          -> AccountApplication    (authorization/balance/position/facts)
          -> RiskApplication       (reserve/release)
          -> ExecutionApplication  (submit/cancel/replace)
```

每个业务模块只接受自己的 command；Strategy 只通过实例级 Execution application boundary 提交策略 Intent，不直接调用其他模块的 service、provider 或 Unix socket。Unix JSON、FlatBuffers 或未来的网络 transport 都只是 envelope 的编码方式，不能各自定义一套字段语义。

## 7. 当前代码迁移顺序

1. 增加公共 `CommandEnvelope`、`CommandResult`、`CommandError` 类型和 schema version 校验。
2. 将 `SubscriptionRequest` 的 selector 逐步升级为结构化对象，并通过 schema version 迁移，不增加旧命令兼容层。
3. 将 `TargetPositionRequest` 改为显式 `account_id`、`segment_key`、instrument reference、source identity 和 order policy。
4. Market、Account、Execution、Risk 的 Unix command handler 统一返回 result/error code。
5. 完成幂等 journal，并删除隐式 account/segment；字符串 operation 不进入 payload，统一由 `operation` 字段表达。
