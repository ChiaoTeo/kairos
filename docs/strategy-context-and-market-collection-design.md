# Strategy Context 与 Market Collection 设计

## 1. 目标与所有权

本设计解决两个彼此独立的问题：Strategy 的临时消费需求，以及 Workspace 的长期行情采集需求。

- Strategy instance 拥有自己的订阅租约；MarketActor 仍是订阅状态的唯一可变状态 owner。
- Strategy 正常停止、启动失败、事件循环失败或由 launch 清理残留进程时，均按
  `(launch_id, instance_id, strategy_id)` 一次性释放 owner 下的全部租约。
- Workspace 拥有长期采集策略。它使用 `collection:<name>` 独立订阅，不依赖任何 Strategy
  是否存活，因此策略退出不会停止被配置为长期保存的 provider 行情需求。
- MarketActor 只拥有实时 current、订阅和 freshness；历史写入由 Market service 内部的独立、
  有界 writer task 完成，避免把文件状态变成第二份业务状态。

## 2. 调研结论

成熟框架的共同点不是把 vendor client 塞给策略，而是提供稳定的业务能力集合：

- NautilusTrader Strategy 提供历史请求、实时订阅、Clock、Cache、Portfolio 以及订单/持仓管理，
  并强调同一策略代码用于 backtest 和 live；其 Cache 在事件回调前更新最新行情。
  参考 [Strategies](https://nautilustrader.io/docs/latest/concepts/strategies/) 与
  [Cache](https://nautilustrader.io/docs/latest/concepts/cache/)。
- QuantConnect LEAN 提供 Securities、Portfolio、Transactions、Schedule、Universe 等能力；
  backtest 使用流式 time frontier，策略不能读取未来数据。
  参考 [Algorithm Engine](https://www.quantconnect.com/docs/v2/writing-algorithms/key-concepts/algorithm-engine/)
  与 [Scheduled Events](https://www.quantconnect.com/docs/v2/writing-algorithms/scheduled-events)。
- Backtrader 将 data feed、strategy、broker 和生命周期组合到同一个引擎中，并支持相同事件式
  Strategy 运行于回测或 live feed。参考 [Cerebro](https://www.backtrader.com/docu/cerebro/)
  与 [Strategy](https://www.backtrader.com/docu/strategy/)。
- VeighNa CTA 明确提供策略初始化、启动、停止、参数编辑和移除生命周期。
  参考 [CtaStrategy](https://www.vnpy.com/docs/cn/community/app/cta_strategy.html)。

Kairos 采用能力分组，但不复制这些框架的全局 Cache、Engine 或 broker owner。业务状态继续由
Market、Account、Execution、Risk 各自 Actor 拥有，StrategyContext 只暴露 application contract。

## 3. 订阅租约生命周期

`context.subscribe(...)` 返回 `SubscriptionLease`。调用者可以提前
`context.unsubscribe(lease)`，但不需要在 `on_end` 手工逐条清理。

```text
Strategy subscribe
  -> Market owner = ["strategy", launch_id, instance_id, strategy_id]
  -> lease ready/pending
  -> Strategy stop/failure/process cleanup
  -> market.release_owner(owner)
  -> logical Strategy subscriptions removed
  -> collection demand remains active
  -> provider unsubscribe only when no remaining demand needs that route
```

释放接口是 owner-scoped、幂等的。单条 unsubscribe 也校验 owner，避免一个策略删除另一个策略
或 Workspace collection 的订阅。Market source connection 的生命周期仍由 Market composition
管理；释放 Strategy 租约不会销毁 source 对象。

## 4. 长期采集配置

在 Workspace manifest 中配置 collection：

```toml
[market.collections.btc-1m]
enabled = true
subject = "BTCUSDT"
selectors = ["bar:1m"]
exchange = "binance"
market_type = "spot"
asset_type = "crypto"
source_id = "binance-spot"
queue_capacity = 4096
```

Market 启动时为每个启用的 collection 建立 Workspace-owned demand。标准化观察写入：

```text
<workspace-data>/market/collections/<name>/events.jsonl
<workspace-data>/market/collections/<name>/manifest.json
```

每个 collection 有独立有界队列和 append writer；慢 collection 会形成显式背压，不会静默丢弃。
启动时逐行校验并恢复已有 append log，停止时 `flush + sync_data`，再原子替换 manifest。
manifest 记录 schema、market、selector、事件数、首尾时间和最后 Market sequence。

当前在线采集的权威落盘格式是永久保留的可恢复 JSONL。已有
`MarketDataApplication.ingest(..., format="parquet")` 负责把完成的采集文件纳入版本化
研究/回放数据集；在线 Actor 不承担 Parquet compaction。有限期 retention 需要先引入 sealed
partition 与数据集生命周期策略，在该边界落地前不暴露一个无法兑现的配置项。

## 5. StrategyContext 公共能力

Strategy 只依赖 `kairospy.strategy.StrategyContextProtocol`：

| 能力 | 当前接口 | 语义 |
|---|---|---|
| 身份与参数 | `identity`, `params` | immutable strategy/launch/instance identity 与 launch 参数 |
| 时间 | `now`, `clock` | backtest/live 统一业务时间、timer；不使用墙上时间作交易决策 |
| 行情 | `market.subscribe`, `quote`, `trade`, `bar`, `history` | current 与长期 collection history；history 不越过当前 time frontier |
| 账户/组合 | `accounts` / `portfolio.current/balance/position` | 只读 Account application projection；两者当前是同一能力别名 |
| 执行 | `orders.target_position/...`, `intent(s)`, `events` | 只提交业务 Intent，并查询 Execution-owned lifecycle |
| 风险 | `risk.current`, `risk.available` | 只读 Risk snapshot；预交易授权仍由 Execution 主链路执行 |
| Reference | `reference` | 标的、市场与交易规则的 module application contract |
| 策略状态 | `state`, `state.checkpoint()` | JSON-only、版本化、原子替换、instance-scoped |
| 可观测性 | `logger`, `event` | 自动携带 strategy/launch/instance 与事件时间上下文 |

旧的 `context.target_position` 等入口暂时保留为兼容别名；新策略优先使用分组后的
`context.orders` 与 `context.market`。Context 不暴露 Unix socket、mmap reader、provider SDK、
Risk service 或 Execution service 实例。

## 6. 验收边界

- 两个 Strategy owner 的订阅互不影响，重复 release 返回成功且不重复删除。
- Strategy 正常/异常退出均触发 owner release；进程已死亡时 launch stop 仍执行 orphan cleanup。
- collection 与 Strategy 订阅同一 market 时，Strategy 退出后 collection demand 和落盘继续存在。
- writer 重启后追加而非覆盖，损坏行导致明确启动失败，不能静默跳过。
- history 查询在 backtest 中过滤晚于 `context.now` 的观察，防止 look-ahead。
- backtest、paper、live 使用同一 StrategyContext 业务接口；composition 只选择具体 endpoint 和
  配置，Python business application 直接调用 infrastructure，不为运行模式增加 Port/Protocol。
