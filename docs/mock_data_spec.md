# Backtest Mock 数据规范

本规范定义可长期用于回测单元测试、状态机测试、场景测试和确定性回放测试的合成数据。Mock 数据只验证软件行为，不作为策略有效性的证据。

## 1. 权威实现

- 生成器：`trading.backtest.mock.make_mock_dataset`
- 场景枚举：`trading.backtest.mock.MockScenario`
- JSON Schema：`tests/fixtures/backtest/dataset.schema.json`
- 场景期望：`tests/fixtures/backtest/scenarios.json`

生成器必须保持确定性。相同 scenario、split 和 start date 必须生成相同 `content_hash`。

## 2. 数据集结构

```text
HistoricalDataset
├── manifest: DatasetManifest
├── definitions: tuple[InstrumentDefinition, ...]
├── contracts: tuple[ContractMetadata, ...]
└── slices: tuple[MarketSlice, ...]
```

### DatasetManifest

必须包含：

- `schema_version`：当前为 1；
- `dataset_id`：稳定且能识别 scenario/split；
- `start`、`end`：带时区；
- `sampling_seconds`；
- `trading_days` 和 `slice_count`；
- contract/quote/Greeks coverage 与 stale rate；
- source 和 market data type；
- code version；
- 对 definitions+contracts+slices 的 SHA-256；
- `split`：development、validation 或 test；
- `synthetic=true`。

### InstrumentDefinition 与 ContractMetadata

每个数据集必须带历史有效的 `InstrumentDefinition`，其中 `InstrumentId` 只保存稳定内部身份，产品字段位于 tagged `ProductSpec`，Venue symbol/tick/lot/min notional 位于 `ListingDefinition`。

每个可交易合约必须提供：

- 唯一 `instrument_id`；
- `last_trade_at`；
- `settlement_at`；
- 明确的 `settlement_type`；
- 到期场景必须提供 `official_settlement`。

### MarketSlice

- `timestamp` 必须带时区并单调不减；
- 相同 timestamp 时 `sequence` 严格递增；
- 股票、现货等产品不要求伪造 `underlying_price`；确有指数/标的参考价时写入 `reference_prices`，且价格必须大于 0；
- 包含当时完整可见的候选合约，不能只含最终成交腿；
- Quote 的 bid/ask/size 缺失必须用 `None`；
- Greeks 缺失必须用 `None`，不能用 0；
- 质量问题必须显式写入 `quality_issues`；
- `snapshot_span_seconds` 记录切片内部时间跨度。

## 3. 数值与时间规则

- 所有价格、数量、Greeks、费率使用 `Decimal`；
- JSON 中 Decimal 使用 `{"$decimal":"..."}`；
- datetime 使用 ISO 8601 和 `{"$datetime":"..."}`；
- date、time、UUID 分别使用 `$date`、`$time`、`$uuid`；
- SPXW mock 使用 `America/New_York`；
- 默认合约乘数为 100；
- 默认组合为卖出 6000 Put、买入 5950 Put；
- 默认入场自然信用为 2.80；
- 新订单只允许在后续 MarketSlice 成交。

## 4. 标准场景

必须长期保留以下场景名称及语义：

1. `no_trade`：策略不产生可成交交易；
2. `profit_target`：下一切片开仓，达到止盈后再下一切片平仓；
3. `stop_loss`：开仓后价差扩大并触发止损；
4. `never_filled`：限价始终未达到，订单最终取消/过期；
5. `missing_quote`：关键腿报价缺失，风险或成交层拒绝；
6. `fee_turns_profit_to_loss`：毛利润不足以覆盖非零费用；
7. `expiry_all_otm`：两腿内在价值为 0；
8. `expiry_short_itm`：短腿 ITM、保护腿 OTM；
9. `expiry_both_itm`：两腿均 ITM，最大损失受宽度限制；
10. `force_close_failure`：结束时无法取得平仓报价，结果不得为 valid。

场景的机器可读预期值保存在 `scenarios.json`。若业务规则有意改变，必须同时修改实现、期望文件和相关测试，并说明语义变化。

## 5. 多资产垂直切片

除 SPXW 场景外，以下永久 fixture 由 `trading.backtest.reference_scenarios` 提供：

- `covered-call`：股票买入、上市 Call 卖出、现金分红、ITM 指派和股票交割；
- `spot-perp-carry`：BTC 现货买入、永续卖空、跨账户划转、Funding、双边平仓；
- 两者都必须分别运行 Conservative 与 Stress，且 Stress 的最终现金不得优于 Conservative；
- 相同输入重复运行必须得到相同 Ledger transaction 数量和 audit hash。

产品成交模型的固定测试契约：

- `EquityTopOfBookFillModel`：方向性盘口、部分成交和停牌；
- `EquityBarFillModel`：订单必须在 bar 开始前 eligible，禁止同 bar 前视；
- `ListedOptionComboFillModel`：组合自然价、Midpoint 对照、Stress 滑点；
- `CryptoOrderBookFillModel`：逐档消耗和确定性 VWAP；
- `PerpetualFillModel`：mark/index divergence 门禁；
- `StressWrapperFillModel`：买入价更高、卖出价更低、费用更高。

## 6. Split 规则

- development：允许开发过程中反复查看和调试；
- validation：参数冻结前用于有限次数验证；
- test：策略与参数冻结后才运行；
- mock 的三种 split 只验证流程隔离，不提供统计意义；
- 真实策略有效性报告必须使用非 synthetic 的时间切分数据。

## 7. Fixture 设计约束

- 每个价格应尽量可手工计算；
- 一个场景只突出一个失败或成功原因；
- 不使用随机数；若未来必须随机，固定 seed 并保存；
- fixture 不依赖当前系统时间；
- 不能调用 IBKR 或网络；
- 测试必须检查现金、持仓、P&L、订单状态和结果有效性，而不只检查“没有异常”；
- Conservative 是主断言，Stress 必须表现得不优于 Conservative；
- Midpoint 只能作为乐观对照。

## 8. Schema 演进

Schema 变更必须增加 `schema_version`。当前实现只承诺读取当前版本；未知或旧版本应明确失败，禁止静默猜测字段。

Mock 数据若用于长期 golden test，内容哈希应纳入断言。只有经过评审的语义变更才能更新 golden hash。
