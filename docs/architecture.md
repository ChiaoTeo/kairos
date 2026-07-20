# Kairos 最终态架构

期权策略研究框架的目标设计、模块契约、迁移路线和完成定义见
[`options_study_architecture.md`](options_study_architecture.md)。本文继续描述整个多资产交易系统已经采用的核心边界。

从市场假设、信号预测、成交代理到可执行回测和实盘验证的统一证据等级与阶段门禁见
[`study_validation_framework.md`](study_validation_framework.md)。
对应代码、数据产物、迁移命令和验收入口见
[`study_validation_implementation.md`](study_validation_implementation.md)。

## 1. 核心边界

系统只有一套领域事实模型：

```text
InstrumentDefinition + ListingDefinition + port-scoped Capabilities
                         |
normalized market/order/execution/lifecycle events
                         |
                      Ledger
                         |
          Portfolio + UnifiedRiskView
```

- `InstrumentId` 只保存稳定内部身份；
- 产品字段只存在于 tagged `InstrumentContractSpec`；
- Venue symbol、external id、tick、lot 和 min notional 只存在于 `ListingDefinition`；
- execution gateway 只消费 Catalog 显式绑定的 `InstrumentId -> ListingDefinition.symbol`，不会从内部 ID 猜测 Venue symbol；
- IBKR/Binance 原始对象不进入 strategy、accounting、risk 或 backtest；
- 余额、持仓、费用、Funding、公司行为和结算都由不可变 Ledger transaction 重建；
- study、backtest、simulation、paper/testnet 和 live fill 最终进入同一个 `LedgerService` reducer。

旧 `kairos/core`、旧单币种 Portfolio 和旧 broker facade 已删除。

## 2. 产品与 Venue

| 产品 | InstrumentContractSpec | Reference/Market | Execution/Account | Lifecycle |
|---|---|---|---|---|
| 股票/ETF | `EquitySpec` | IBKR + normalized series | IBKR/simulation | 分红、拆股、股票股利、合并、分拆、改名、退市、借券利息 |
| 上市期权 | `ListedOptionSpec` | IBKR + SPXW 专用切片 | IBKR native combo/simulation | 到期、提前行权、指派、physical/cash settlement、adjusted option |
| 加密现货 | `CryptoSpotSpec` | Binance REST/WebSocket/history | Binance testnet/live/simulation | 多资产费用、锁定余额、充值/提现/划转 Ledger |
| 永续 | `PerpetualSpec` | Binance linear/inverse | Binance testnet/live/simulation | Funding、mark/index、强平、ADL |
| 交割合约 | `FutureSpec` | Binance delivery reference | Binance testnet/live/simulation | 到期与最终现金结算 |
| 加密期权 | `CryptoOptionSpec` | Binance option ticker/Greeks/IV | Binance live-only limit execution/account | settlement asset 现金结算 |

IBKR 不是期权专用 connector：

- `IbkrReferenceDataClient` 通过 `ReferenceDataRequest.product_type` 分别同步股票、ETF 和上市期权；
- `IbkrMarketDataClient`、`IbkrExecutionGateway`、`IbkrAccountGateway` 消费通用定义；行情客户端提供 quote、recent trade 和 historical bar；
- Reference、MarketData、Execution 分别声明 capability，指数可查询行情但不会被误路由到执行层；
- `IbkrSpxwOptionChainProvider` 的命名明确表明它只负责 SPX/SPXW option-chain 研究切片；
- 通用 `MarketSnapshot` 不包含强制 `underlying_price`，股票、ETF 和加密产品直接保存 instrument quote；确有参考价格时使用 `reference_prices`。

Capability 以 port 边界为准，而不是描述 Venue 的能力全集：

- `ReferenceCapabilities` 只声明可发现和绑定的 `product_types`；
- `MarketDataCapabilities` 只声明 `market_data`、`product_types` 和原生 Greeks；
- `ExecutionCapabilities` 只声明订单类型、产品类型、组合单、只减仓、post-only、保证金与持仓模式；
- Binance 在上述端口边界内再按 spot、futures、options 产品线细分，禁止跨端口复用 capability；
- 不保留宽泛的 `VenueCapabilities` 兼容类型，新增 connector 必须在类型层面选择所属端口。

## 3. 策略与执行

策略只产生经济 Intent：

- `TargetPositionIntent`；
- `CoveredCallIntent` / `ProtectivePutIntent`；
- `CashAndCarryIntent`；
- `HedgeIntent` / `TransferIntent` / `CancelIntent`；
- 结构化 option Intent。

`strategy_planner` 将 Intent 转换为带 strategy/intent/correlation id 的类型化执行计划：普通订单、组合订单候选、转账和撤单分别承载。组合订单优先使用 Venue native combo；Venue 不支持时默认拒绝，只有显式 `LeggingPolicy.SEQUENTIAL` 和 naked-leg limit 才能拆腿。

`ExecutionRouter` 在下单前强制检查：

- account Venue 与 listing；
- execution gateway product/order capability；
- tick、lot、minimum quantity、minimum notional；
- post-only/reduce-only capability；
- maximum order quantity/notional；
- kill switch 后 reduce-only 必须真实缩小现有仓位。

普通订单和 native combo 都必须经过 `ExecutionCoordinator` 的 readiness、reconciliation、持久幂等与 kill switch；`CancelIntent` 会与原 client order、Strategy Intent 和 Venue order id 一并写入事件日志。

## 4. 回测与研究

SPXW `BacktestEngine` 使用 Catalog 和 Ledger-backed `BacktestPortfolio`，保留确定性 clock/feed、无同 slice 成交、风险决策、到期结算、审计 hash 和 replay。

产品成交模型独立：

- `EquityTopOfBookFillModel`；
- `EquityBarFillModel`；
- `ListedOptionComboFillModel`；
- `CryptoOrderBookFillModel`；
- `PerpetualFillModel`；
- `DeliveryFutureFillModel`；
- `CryptoOptionFillModel`；
- `StressWrapperFillModel`。

`covered-call` 与 `spot-perp-carry` reference scenarios 使用正式 strategy 和同一个 Ledger reducer，固定输出 Conservative/Stress 与 audit hash。Synthetic 结果只证明机制，不证明收益。

## 5. 运行安全

- 外部状态命令必须显式指定 `paper`、`testnet` 或 `live`；
- `live` 下单额外要求 `--confirm-live`；
- Binance 凭据只从环境变量读取，系统没有 Venue withdrawal API；
- 启动必须通过 Catalog、market data、account、execution 和 reconciliation readiness；
- client order id 重启后从 persistent event log 恢复，不允许同 id 对应不同请求；
- public stream 归一化为共享 Quote/Trade/OrderBook/DerivativeMarketState；private stream、REST fill/funding backfill 和 Ledger ingestion 对外部事件去重；
- Binance connector 包含 rate limit、clock sync、WebSocket reconnect、REST backfill、账户/订单/持仓恢复；
- kill switch 撤销全部 working orders 后进入严格只减仓；
- clock skew、rate limit、disconnect 和 authentication error 进入 operational alerts。

## 6. 验收证据

| 验收面 | 自动化证据 |
|---|---|
| Catalog 历史版本、InstrumentContractSpec、capability | `test_multi_asset_domain.py` |
| IBKR 股票/期权分离、股票 quote/trade/bar 与 connector capability | `test_ibkr_connectors.py` |
| 多账户、多资产、conversion、Ledger 重启重建 | `test_ledger_portfolio_v2.py` |
| 股票/期权公司行为、exercise/assignment、Protective Put | `test_equity_options.py`, `test_product_events.py` |
| linear/inverse/quanto、Funding 回补、delivery future、crypto option | `test_crypto_products.py`, `test_product_events.py` |
| 股票/期权/现货/永续/交割合约/加密期权 Conservative/Stress 与无前视 | `test_backtest_fill_contracts.py`, `test_multi_asset_backtest.py`, `test_reference_scenarios.py` |
| SPXW reference、split、replay/audit hash | `test_backtest_engine.py`, `test_settlement.py` |
| 股票+加密通用 MarketSnapshot | `test_normalized_series.py` |
| reconnect/backfill、重复 fill、rate limit/clock skew | `test_crypto_products.py` |
| 完整 Intent、native combo、Cancel、readiness、reconciliation、重启幂等、kill switch | `test_orchestration.py` |
| CLI 体验与 live 二次确认 | `test_cli_multi_asset.py` |
| 可选真实 Venue contract | `test_ibkr_integration.py`, `test_binance_testnet.py` |

标准验收命令：

```bash
./pyenv/bin/python -m compileall -q kairos tests
./pyenv/bin/python -m unittest discover -s tests -v
git diff --check
```
