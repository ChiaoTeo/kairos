# 项目命名专业度审计与改名建议

本文基于当前工作区的目录、公开 API、CLI、示例、研究脚本和文档命名做一次命名审计。目标不是追求“更好听”，而是让命名更接近专业量化团队常用的语义：身份稳定、层次清晰、研究与生产边界明确、供应商/交易场所/经济产品不混用。

## 总体结论

项目的核心领域命名已经有较好的专业基础，尤其是 `InstrumentDefinition`、`ListingDefinition`、`ReferenceCatalog`、`DatasetRelease`、`QualityLevel`、`Intent`、`ExecutionPlan`、`Ledger Transaction`、`Portfolio / Risk View` 这一组概念，基本符合机构级量化系统的分层方式。

如果产品名确定为 `kairos`，建议把它作为产品品牌和包命名空间的核心，而不是再使用泛泛的 `trader`。`Kairos` 本身有“关键时机 / 时机判断”的含义，和量化交易中择时、信号、执行窗口、风险门禁都比较贴合，适合作为平台名。

主要问题集中在四类：

1. 项目顶层名称偏泛：`trader` / `trading` 容易被理解成普通交易脚本，而不是研究、数据、执行和账户事实系统；建议统一升级为 `kairos`。
2. 研究与正式系统混在同一公开包中：根目录 `research/` 和 `trading/research/` 并存，`ResearchSpec` 默认绑定 SPXW/IBKR，语义不够通用。
3. 示例、fixture、mock、SMA 教程命名在 CLI 和模块中有扩散：适合教学，但不适合作为长期公共 API 的命名中心。
4. 供应商命名 `massive` 贯穿数据集、模块、命令和文档，和专业数据平台常见的 vendor/provider 抽象相比偏“供应商耦合”。
5. `adapter`、`service`、`models`、`base`、`run` 等词过于笼统。它们在小脚本里可接受，但在量化平台里会掩盖真实职责，导致新人不知道这是数据连接、执行网关、协议端口、业务服务还是存储仓库。

另有仓库卫生问题：当前项目中存在 `__pycache__`、`.pyc`、`.pytest_cache`、`pyenv/` 等本地运行产物。即使 `.gitignore` 已配置忽略，它们仍出现在工作区扫描结果里，会降低命名审计、代码搜索和打包判断的可信度。

## 建议命名原则

| 原则 | 推荐做法 | 避免做法 |
| --- | --- | --- |
| 先命名业务身份，再命名技术实现 | `InstrumentId`、`DatasetRelease`、`ExecutionRoute` | `symbol`、`file`、`adapter_data` |
| 区分经济产品、挂牌、数据供应商和交易场所 | `ProductId`、`ListingId`、`ProviderId`、`VenueId` | 用 `BTCUSDT` 或 `massive` 代表全部 |
| 研究、回测、仿真、影子、生产分层命名 | `research`、`backtest`、`historical_simulation`、`shadow`、`live` | `paper-sma`、`mock backtest` 作为正式概念扩散 |
| fixture 只用于测试/验收语义 | `fixture`、`synthetic_fixture`、`reference_scenario` | `mock` 进入正式数据、策略或 CLI 主路径 |
| 公共 API 避免供应商名外泄 | `market_data_provider`、`vendor_archive`、`provider_symbol_mapping` | `massive_day_aggs` 成为业务概念 |
| 缩写只在金融行业高度通用时使用 | `SMA`、`IV`、`VRP`、`DTE`、`PnL` | `L4`、`spec`、`run` 单独承担复杂业务含义 |
| 少用笼统容器词 | `ExecutionGateway`、`MarketDataClient`、`ReferenceDataRepository` | `adapter`、`service`、`manager`、`handler`、`utils`、`models` |

## 产品命名建议：Kairos

既然产品名确定为 `kairos`，推荐形成下面这套一致命名：

| 层级 | 建议命名 | 说明 |
| --- | --- | --- |
| 产品品牌 | `Kairos` | 对外展示、人类文档、README 标题使用首字母大写 |
| Python 包 | `kairos` | 替代当前 `trading`；短、稳定、不泛 |
| CLI | `kairos` | 替代或并行当前 `trader`；例如 `kairos data search`、`kairos run backtest` |
| 项目发布名 | `kairos` 或 `kairos-quant` | 如果 PyPI 或内部包仓库已有冲突，用 `kairos-quant` |
| 兼容别名 | `trader` | 可以保留一个版本周期，提示迁移到 `kairos` |

不建议使用 `kairos_trading` 作为主包名，因为它又把系统缩回“交易”一层；当前项目已经覆盖 research、data、pricing、risk、treasury、execution、ledger，`kairos` 单独作为平台命名更干净。

建议的文档标题从：

```text
# Trader
```

改为：

```text
# Kairos
```

建议的 CLI 从：

```text
trader run backtest --strategy sma-cross-v1@1.2.0
```

逐步迁移为：

```text
kairos run backtest --strategy sma-cross-v1@1.2.0
```

## `bar` 与 `OHLCV` 的命名取舍

`bar` 和 `OHLCV` 很接近，但不完全等价：

| 名称 | 含义 | 适合用在 |
| --- | --- | --- |
| `OHLCV` | 明确表示字段集合：open、high、low、close、volume | 数据集、schema、字段组、文件目录，例如 `market.ohlcv.equity.us.1d` |
| `Bar` | 表示一个时间桶里的行情聚合对象，通常包含 OHLCV，也可能带 vwap、trade_count、notional_volume、source latency | 代码类型、事件、回测输入对象，例如 `Bar`, `BarSeries`, `CanonicalBarEvent` |
| `DailyBar` / `IntradayBar` | 表示具体频率或业务用途的 bar | 领域对象、API 返回类型 |
| `Candle` | 零售交易软件常用，专业系统里不如 `Bar` 稳重 | UI 或图表层可以用，核心领域不建议 |

建议规则：

1. 数据产品名和目录优先使用 `ohlcv`，因为它清楚说明数据 schema。
2. 代码对象优先使用 `Bar`，因为它表达“一个时间窗口的市场聚合对象”。
3. 如果数据缺少 volume，就不要叫 `OHLCV`，应叫 `OHLC`、`QuoteBar` 或 `MidPriceBar`。
4. 如果 bar 来自报价而非成交，命名要写清楚：`QuoteBar`、`MidQuoteBar`、`MarkPriceBar`，不要泛称 `Bar` 后再让读者猜。
5. `day_aggs` 是供应商 API 口径，不建议进入公共领域命名；平台侧应改为 `daily_ohlcv` 或 `daily_bars`。

因此当前项目里可以保留 `Bar` / `BarSeries` 作为代码层对象，但 DatasetKey、CLI、文档中的长期数据产品建议统一到 `ohlcv`：

```text
market.ohlcv.equity.us.1d.raw
market.ohlcv.crypto.binance.perpetual.1h.raw
```

## `adapter` 的歧义与替代命名

`adapter` 的问题是它只说明“做了适配”，没有说明适配的是什么边界。在量化系统里，这个词至少可能指：

| 实际职责 | 不建议命名 | 推荐命名 |
| --- | --- | --- |
| 访问供应商行情/参考数据 API | `MassiveAdapter` | `MassiveMarketDataClient`、`MassiveReferenceDataClient`、`MassiveVendorArchiveClient` |
| 把供应商原始响应转成内部事件 | `MassiveAdapter` | `MassiveMarketDataNormalizer`、`MassiveEventDecoder` |
| 接交易所/券商下单接口 | `BinanceAdapter`、`IbkrAdapter` | `BinanceExecutionGateway`、`IbkrExecutionGateway` |
| 账户和余额查询 | `AccountAdapter` | `AccountGateway`、`BrokerAccountClient`、`VenueAccountClient` |
| 参考数据同步 | `ReferenceAdapter` | `ReferenceDataSyncClient`、`InstrumentReferenceIngestor` |
| 端口协议定义 | `BaseAdapter` | `ExecutionPort`、`MarketDataPort`、`ReferenceDataPort` |
| 测试替身 | `FakeAdapter` | `InMemoryExecutionGateway`、`DeterministicMarketDataSource` |

建议规则：

1. 目录可以从 `adapters/` 逐步改为 `connectors/` 或按职责拆分为 `gateways/`、`clients/`、`normalizers/`。
2. 类名不要只叫 `XxxAdapter`；必须说清楚它是 `Client`、`Gateway`、`Normalizer`、`Ingestor`、`Repository` 还是 `Port`。
3. `Gateway` 用于会产生外部副作用的边界，例如下单、撤单、转账。
4. `Client` 用于读取外部 API 或下载文件。
5. `Normalizer` / `Decoder` 用于供应商格式到 canonical schema 的转换。
6. `Port` 用于内部协议，不绑定具体供应商。
7. `Connector` 可以作为目录或组合对象名，但最好也带职责：`MarketDataConnector`、`ExecutionConnector`。

推荐迁移方向：

```text
trading/adapters/binance/adapter.py
-> kairos/connectors/binance/execution_gateway.py
-> kairos/connectors/binance/account_gateway.py
-> kairos/connectors/binance/market_data_client.py
-> kairos/connectors/binance/reference_data_client.py

trading/adapters/massive/day_aggs.py
-> kairos/connectors/massive/daily_ohlcv_client.py
-> kairos/connectors/massive/daily_ohlcv_ingestor.py

trading/adapters/base.py
-> kairos/ports/execution.py
-> kairos/ports/market_data.py
-> kairos/ports/reference_data.py
```

## 笼统词禁用/慎用清单

下面这些词不是绝对不能用，但不能单独承担业务语义。使用时必须加上明确对象或职责。

| 笼统词 | 问题 | 更好的方向 |
| --- | --- | --- |
| `adapter` | 不说明连接、转换、执行、账户还是参考数据 | `ExecutionGateway`、`MarketDataClient`、`EventNormalizer` |
| `service` | 不说明业务动作 | `PricingService` 可接受；更好是 `OptionValuationService`、`DatasetPublicationService` |
| `manager` | 通常表示职责不清 | `RiskLimitRegistry`、`OrderStateStore`、`StrategyReleaseRepository` |
| `handler` | 不说明处理什么事件、产生什么结果 | `OrderAckHandler`、`FillIngestionHandler` |
| `processor` | 批处理/转换含义模糊 | `CorporateActionAdjuster`、`OhlcvNormalizer` |
| `engine` | 可接受但需慎用，容易变成万能类 | `BacktestEngine` 可接受；避免 `DataEngine`、`TradingEngine` |
| `models` | 文件名过泛 | `definitions`、`states`、`requests`、`events`、`schemas` |
| `base` | 只说明继承关系，不说明协议 | `ports`、`protocols`、`interfaces` |
| `utils` / `helpers` | 容易堆杂物 | 按领域拆成 `time_rules`、`decimal_normalization`、`symbol_parsing` |
| `run` | 既可指命令、执行实例、回测结果，也可指函数 | `StrategySession`、`RunArtifact`、`ExecutionAttempt` |
| `data` | 太宽 | `market_data`、`reference_data`、`research_dataset`、`ledger_data` |
| `product` | 在本项目中同时有经济产品和数据产品 | `EconomicProduct`、`DataProductDefinition` |

## 高优先级改名建议

| 当前命名 | 位置/范围 | 问题 | 建议命名 | 迁移优先级 |
| --- | --- | --- | --- | --- |
| `trader` | `pyproject.toml` project name、CLI script | 偏个人工具名，不体现系统是量化研究与交易基础设施 | 改为 `kairos`；可保留 `trader` 作为兼容 CLI 别名 | P1 |
| `trading` | 顶层 Python 包 | 过泛，容易和下单执行层混淆；当前包实际包含 data、research、risk、treasury、execution | 改为 `kairos`；如果内部包仓库冲突，可用 `kairos_quant` | P1 |
| `research/` 与 `trading/research/` | 顶层研究脚本和包内研究服务 | 两个同名层级语义不同：一个像策略实验仓，一个像平台服务 | 顶层改为 `studies/` 或 `research_projects/`；包内保留 `trading.research` 或改为 `trading.research_platform` | P1 |
| `ResearchSpec` | `trading/research/spec.py` | 名称通用，但字段默认绑定 SPX/SPXW/IBKR 期权链采集 | 改为 `OptionChainCaptureSpec`、`SpxwOptionResearchSpec` 或 `OptionUniverseSelectionSpec` | P1 |
| `ResearchService.capture` | `trading/research/service.py` | capture 是采集行为，但该服务还做 discover、select、snapshot、analyze，名称过宽 | 拆分或改名为 `OptionResearchCaptureService.capture_snapshot` / `ResearchSnapshotService.create_snapshot` | P2 |
| `trading/backtest/mock.py` | 回测数据生成 | `mock` 在专业项目里通常表示测试替身，不应承载可复用参考场景 | 改为 `synthetic_scenarios.py`、`reference_scenarios.py` 或 `deterministic_fixtures.py`；`MockScenario` 改为 `SyntheticScenario` | P1 |
| `make_mock_dataset` | `trading/backtest/mock.py` | 容易让人误以为结果可作为研究证据；与文档中 fixture 不能证明收益的原则冲突 | `build_synthetic_backtest_dataset` 或 `build_reference_scenario_dataset` | P1 |
| `paper-sma`、`shadow-sma`、`simulate-sma` | CLI 和文档 | 将运行模式和具体示例策略耦合；长期会让 CLI 成为示例集合 | 推荐统一为 `trader run paper --strategy sma-cross-v1`、`trader run shadow --strategy ...`、`trader run simulate --strategy ...` | P1 |
| `sma_strategy.py` 与 `sma_cross.py` | `trading/strategies/` | 两个文件都表达 SMA，边界不直观：一个是正式策略协议，一个是教学/批量回测函数 | `sma_cross_strategy.py` 和 `sma_cross_research_backtest.py`；或将批量函数移到 `trading/backtest/examples/sma_cross.py` | P2 |
| `main.py` | 仓库根目录 | 只有入口转发语义时，根目录 `main.py` 显得脚手架化 | 删除或改为只保留 `python -m trading`；若保留，命名为 `run_cli.py` | P3 |

## 中优先级改名建议

| 当前命名 | 位置/范围 | 问题 | 建议命名 |
| --- | --- | --- | --- |
| `massive` | `trading/adapters/massive/*`、CLI `massive-*`、数据集 ID | 供应商名贯穿过深，未来替换或并行供应商会扩大迁移成本 | 代码包可保留在 connector 层，但 CLI 和 DatasetKey 建议用 `provider` 维度表达，如 `data vendor-fetch --provider massive` |
| `day_aggs` | Massive 适配器、CLI、数据集 | vendor API 叫法，行业里更常见 `daily_bars` 或 `daily_ohlcv`；数据产品名更建议 `ohlcv` | `daily_ohlcv` 用于数据产品；`DailyBar` / `BarSeries` 用于代码对象 |
| `option_day_iv` | `trading/adapters/massive/option_iv.py`、CLI | “day IV”不如“close-based implied volatility”明确 | `close_implied_volatility`、`option_close_iv_surface` |
| `market_slice` | `trading/data/market_slice_*`、`trading/backtest/feed.py` | 对外语义不够标准，像内部实现细节 | 若表示同一时间截面的多标的快照，建议 `market_snapshot`；若表示时间窗口，建议 `market_window` |
| `HistoricalDataset` | `trading/backtest/feed.py` | 名称太大，实际像期权链回测用的快照序列数据集 | `BacktestDataset`、`MarketReplayDataset` 或 `OptionSurfaceReplayDataset` |
| `ContractMetadata` | `trading/backtest/feed.py` | metadata 太泛，内容涉及到期、结算价、现金/实物等生命周期事实 | `InstrumentLifecycleSnapshot`、`SettlementMetadata` |
| `DatasetProduct` | `trading/data/models.py` | 与 `EconomicProduct` 容易混淆，虽然文档已有解释 | 可保留，但文档和 API 中多使用 `DataProduct` 别名；长期可改为 `DataProductDefinition` |
| `ProductSpec` | `trading/domain/product.py` | 与 `DatasetProductSpec` 同名后缀，跨数据/交易领域阅读时容易混淆 | 经济产品侧可改为 `InstrumentContractSpec` 或保持 `ProductSpec` 但避免数据侧使用同后缀 |
| `RunMode.LIVE_PAPER` | `trading/data/models.py` | paper 是账户/环境语义，live-paper 容易和外部 paper/testnet 混淆 | `PAPER_TRADING` 或 `SIMULATED_EXECUTION_LIVE_MARKET`；文档中可保留解释 |
| `application/runtime_golden.py` | 应用层 | golden 是测试语义，放应用层显得不够生产 | `runtime_reference_artifact.py` 或移至 tests/reference |
| `runtime_failure_matrix.py` | 应用层 | “matrix”像测试报告，不像运行时组件 | `runtime_failure_policy.py`、`startup_readiness_policy.py` |
| `task_supervisor.py` | 应用层 | task 是技术执行单位，量化运行时更关注 service/session/process | `service_supervisor.py` 或 `runtime_service_supervisor.py` |

## 低优先级但建议统一的命名

| 当前命名 | 问题 | 建议 |
| --- | --- | --- |
| `adapter.py` 在多个供应商目录中重复 | IDE 标签页和搜索结果不直观，且 `adapter` 不说明职责 | `execution_gateway.py`、`market_data_client.py`、`event_normalizer.py`、`reference_data_client.py` |
| `base.py` | Python 项目常见，但公共领域语义弱 | `protocols.py`、`interfaces.py`、`ports.py` |
| `service.py` | 过泛，多个包都有 `service.py` | 用业务动作命名，如 `dataset_publication.py`、`option_valuation.py`、`research_capture.py` |
| `models.py` | 过泛，但在小包内可接受 | 领域核心可拆为 `definitions.py`、`events.py`、`states.py`、`requests.py` |
| `analyzer.py` | 分析对象不明确 | `option_snapshot_analyzer.py`、`research_evidence_analyzer.py` |
| `selector.py` | 选择什么不明确 | `option_universe_selector.py` 或 `instrument_selector.py` |
| `readiness.py` / `health.py` / `doctor` | 三者边界接近 | 统一命名层次：`health_check`、`readiness_gate`、`diagnostics` |
| `btcusdt-depth-...` 输出文件 | Venue symbol 直接出现在产物名里可接受，但不应作为内部身份 | 产物 manifest 中必须同时写 `InstrumentId`、`ListingId`、`VenueId` |

## 专业命名词典建议

### 资产与参考数据

| 概念 | 推荐英文命名 | 说明 |
| --- | --- | --- |
| 资产/记账单位 | `AssetId`、`AssetDefinition` | USD、BTC、USDT 等现金或资产单位 |
| 经济产品 | `EconomicProduct`、`ProductId` | 跨 venue 的经济合约身份 |
| 合约条款 | `ContractSpec`、`InstrumentContractSpec` | 期权、期货、永续、现货的条款 |
| 可交易/可定价标的 | `InstrumentId`、`InstrumentDefinition` | 内部稳定身份 |
| 交易场所挂牌 | `ListingId`、`ListingDefinition` | Venue symbol、tick、lot、交易规则 |
| 供应商映射 | `ProviderSymbolMapping` | 数据供应商符号映射，不等于 venue listing |
| 交易日历 | `TradingCalendar`、`CalendarId` | 避免只叫 `calendar` |

### 数据平台

| 概念 | 推荐英文命名 | 说明 |
| --- | --- | --- |
| 逻辑数据产品 | `DataProductDefinition` / `DatasetProduct` | 二选一后全项目统一 |
| 不可变发布 | `DatasetRelease` | 推荐保留 |
| 浮动别名 | `ReleaseAlias` | 比单独 `Alias` 更明确 |
| 原始供应商归档 | `VendorArchive` / `SourceArchive` | `SourceArchive` 当前可接受 |
| 标准化事件 | `CanonicalMarketEvent` | 推荐保留 canonical 语义 |
| 点时数据 | `PointInTimeDataset` | 强调 `available_time` |
| 数据质量门禁 | `DataQualityGate`、`QualityProfile` | 推荐保留 |

### 研究与策略

| 概念 | 推荐英文命名 | 说明 |
| --- | --- | --- |
| 研究课题 | `Study`、`ResearchStudy` | 不等于 `Strategy` |
| 假设 | `Hypothesis` | 可以进入 study manifest |
| 特征 | `Feature` | 数据变换产物 |
| 因子 | `Factor` | 可复用、带 point-in-time 语义的信号 |
| 策略定义 | `StrategySpec`、`StrategyRelease` | 推荐保留 |
| 策略实例 | `StrategySession`、`StrategyRuntime` | 不建议只叫 `Run` |
| 决策 | `StrategyDecision` | 推荐保留 |
| 意图 | `Intent` | 推荐保留 |

### 执行、账户与风控

| 概念 | 推荐英文命名 | 说明 |
| --- | --- | --- |
| 执行计划 | `ExecutionPlan` | 推荐保留 |
| 订单请求 | `OrderRequest` / `OrderCommand` | 二选一，避免混用 |
| 订单状态 | `OrderState` | 推荐保留 |
| 成交事实 | `TradeExecution` 或 `Fill` | 当前两者并存，建议规定：外部成交回报叫 `TradeExecution`，账务归约可叫 `Fill` |
| 账本 | `LedgerBook`、`LedgerTransaction` | 推荐保留 |
| 持仓视图 | `PositionView`、`PortfolioView` | 强调 derived view |
| 风险限制 | `RiskLimits` | 推荐保留 |
| Kill switch | `KillSwitch` | 行业常用，可保留 |
| 对账 | `ReconciliationService` | 推荐保留 |

## 推荐迁移路线

### 第一阶段：不破坏 API 的治理

1. 新增命名规范文档，并在 README 的“核心命名与边界”处链接。
2. 确立 `Kairos` / `kairos` 为产品、包和 CLI 的目标命名，保留 `trader` / `trading` 兼容别名。
3. 清理本地运行产物：`__pycache__`、`.pyc`、`.pytest_cache`、`pyenv/` 不应进入版本库或审计范围。
4. 给高风险旧名加 alias 和 deprecation 注释：例如 `MockScenario = SyntheticScenario`、`make_mock_dataset = build_synthetic_backtest_dataset`。
5. CLI 新增通用命令，同时保留旧命令：`run shadow --strategy ...` 兼容 `run shadow-sma`。
6. 明确 `OHLCV` 用于数据产品/schema，`Bar` 用于代码对象和市场事件。

### 第二阶段：公共 API 语义收敛

1. 将顶层 `research/` 改为 `studies/` 或 `research_projects/`。
2. 将 `ResearchSpec` 拆成更具体的 `OptionChainCaptureSpec` / `OptionUniverseSelectionSpec`。
3. 将 `trading/backtest/mock.py` 改为 `trading/backtest/synthetic_scenarios.py`。
4. 将 `adapter` 类名按职责改为 `Client`、`Gateway`、`Normalizer`、`Ingestor`、`Port`。
5. 梳理 `HistoricalDataset`、`MarketSlice`、`ContractMetadata` 的边界，决定是否改为 replay/snapshot/settlement 语义。

### 第三阶段：平台级命名升级

1. 将 Python 包从 `trading` 迁移到 `kairos`。
2. 将供应商名从 CLI 主路径中下沉到 `--provider massive` 这类参数。
3. 统一 DatasetKey 格式，避免 `provider` 出现在过高层级。例如优先使用 `market.ohlcv.equity.us.1d.raw` + `provider=massive` 维度，而不是把 `massive` 固定在逻辑产品名中。
4. 将目录 `adapters/` 拆分或迁移为 `connectors/`、`ports/`、`gateways/`，让边界职责在路径上可见。

## 批量调整方案

建议按 7 个批次推进。原则是：先加新名和兼容层，再逐步迁移调用方，最后删除旧名。不要一次性把包名、CLI、目录、类名、数据集 ID 全部改掉，否则测试失败时很难定位。

### Wave 0：命名冻结与自动检查

目标：先阻止新旧命名继续扩散。

典型改动：

| 动作 | 内容 |
| --- | --- |
| 建立命名决策记录 | 在 `docs/` 增加或链接本文，明确 `Kairos`、`OHLCV/Bar`、`Gateway/Client/Port` 规则 |
| 增加禁用词扫描 | 在 hygiene test 中扫描新增文件名和公开类名，提示 `adapter`、`manager`、`utils`、裸 `service`、裸 `models` |
| 建立兼容策略 | 规定旧名至少保留一个迁移周期，旧入口只做转发和提示 |

验收标准：

1. 新文档通过评审。
2. 新增命名不得继续引入裸 `adapter`、`utils`、`manager`。
3. 测试不要求大规模改名，但能提醒后续新增命名。

### Wave 1：品牌和 CLI 兼容入口

目标：把产品名切到 `Kairos`，但不破坏现有脚本。

典型改动：

| 当前 | 新命名 | 兼容方式 |
| --- | --- | --- |
| README `# Trader` | `# Kairos` | README 说明 `trader` 是旧 CLI 别名 |
| `pyproject.toml` script `trader` | 增加 script `kairos` | `trader = "trading.__main__:main"` 暂时保留 |
| 文档命令 `trader ...` | 新文档优先 `kairos ...` | 旧命令放在兼容说明里 |

验收标准：

1. `kairos --help` 和 `trader --help` 输出一致。
2. README 首屏使用 `Kairos`。
3. CI 和现有示例不因 CLI 改名失败。

### Wave 2：fixture/mock 命名收敛

目标：让测试/教学数据不再看起来像正式研究证据。

典型改动：

| 当前 | 新命名 | 兼容方式 |
| --- | --- | --- |
| `trading/backtest/mock.py` | `trading/backtest/synthetic_scenarios.py` | 旧文件保留薄转发 |
| `MockScenario` | `SyntheticScenario` | `MockScenario = SyntheticScenario` |
| `make_mock_dataset` | `build_synthetic_backtest_dataset` | 旧函数转发并标注 deprecated |
| CLI `backtest mock` | `backtest synthetic-scenario` | 旧命令保留 |
| Dataset ID `mock-*` | `synthetic-*` | 旧测试夹具允许读取 |

验收标准：

1. 所有生产文档不再把 `mock` 当正式流程入口。
2. 测试仍可通过旧导入。
3. 新代码只能使用 `SyntheticScenario` 和 `build_synthetic_backtest_dataset`。

### Wave 3：OHLCV 与 Bar 统一

目标：数据层使用 `ohlcv`，代码对象保留 `Bar`。

典型改动：

| 当前 | 新命名 | 说明 |
| --- | --- | --- |
| `day_aggs` | `daily_ohlcv` | 数据产品、CLI、文件名优先使用 |
| `equity_day_aggs.py` | `equity_daily_ohlcv.py` | 如果内容是 OHLCV schema |
| `prepare-massive-equity-day-aggs` | `prepare-equity-daily-ohlcv --provider massive` | 旧命令保留 |
| `Bar` / `BarSeries` | 保留 | 代码对象命名专业且直观 |
| quote 聚合 bar | `QuoteBar` / `MidQuoteBar` | 避免误认为成交 OHLCV |

验收标准：

1. DatasetKey 新增命名统一为 `market.ohlcv...`。
2. 代码里 `Bar` 类型不被机械改成 `OHLCV`。
3. 供应商术语 `day_aggs` 只留在 connector 内部或兼容层。

### Wave 4：adapter 拆成职责命名

目标：把 `adapter` 从公共类名和核心路径中降级，改成更具体的边界职责。

典型改动：

| 当前 | 新命名 | 用途 |
| --- | --- | --- |
| `adapters/base.py` | `ports/execution.py`、`ports/market_data.py`、`ports/reference_data.py` | 内部协议 |
| `BinanceExecutionAdapter` | `BinanceExecutionGateway` | 下单/撤单等外部副作用 |
| `BinanceAccountAdapter` | `BinanceAccountGateway` | 账户、余额、仓位读取 |
| `BinanceMarketDataAdapter` | `BinanceMarketDataClient` | 行情读取 |
| `IbkrReferenceAdapter` | `IbkrReferenceDataClient` | 合约和参考数据 |
| `MassiveSourceArchive` | `MassiveVendorArchiveClient` 或 `MassiveSourceArchiveClient` | 供应商归档读取 |
| decoder 类 | `EventDecoder` / `MarketDataNormalizer` | 原始响应到 canonical |

兼容策略：

1. 先新增新类名，旧类名继承或别名到新类。
2. 更新内部导入到新名。
3. 最后保留旧名仅供外部兼容。

验收标准：

1. 公开新 API 不出现裸 `Adapter`。
2. 会产生外部副作用的类统一使用 `Gateway`。
3. 只读取外部 API 的类统一使用 `Client`。
4. 协议层统一使用 `Port`。

### Wave 5：research/studies 边界整理

目标：区分平台研究能力和具体研究项目。

典型改动：

| 当前 | 新命名 | 说明 |
| --- | --- | --- |
| 顶层 `research/` | `studies/` | 具体研究项目、实验、报告 |
| `trading/research/` | 暂保留或改为 `kairos/research/` | 平台研究服务 |
| `ResearchSpec` | `OptionUniverseSelectionSpec` / `OptionChainCaptureSpec` | 避免通用名绑定 SPXW |
| `ResearchService` | `OptionResearchCaptureService` / `ResearchSnapshotService` | 按实际职责拆分 |

验收标准：

1. 顶层研究项目与包内研究服务不再同名。
2. SPXW/IBKR 相关默认值不再藏在通用 `ResearchSpec` 中。
3. README 对 `Study`、`Factor`、`Strategy` 的边界更清楚。

### Wave 6：包名从 `trading` 迁移到 `kairos`

目标：完成平台命名升级。

典型改动：

| 当前 | 新命名 | 兼容方式 |
| --- | --- | --- |
| `trading/` | `kairos/` | `trading` 保留兼容包，转发到 `kairos` |
| `from trading...` | `from kairos...` | 内部导入全部迁移 |
| `python -m trading` | `python -m kairos` | 旧入口保留 |
| package include `trading*` | `kairos*` + `trading*` 兼容 | 一个迁移周期后删除旧包 |

验收标准：

1. 内部源码不再从 `trading` 导入。
2. `python -m kairos --help` 正常。
3. `python -m trading --help` 仍能兼容运行。
4. 测试、示例、文档都优先使用 `kairos`。

### Wave 7：删除旧名与最终收口

目标：清理兼容层，形成干净命名。

删除条件：

1. 旧 CLI、旧导入、旧 DatasetKey 已经过至少一个迁移周期。
2. 文档、示例、测试都没有主动使用旧名。
3. release notes 明确列出删除项和替代项。

建议删除项：

| 旧名 | 删除前替代 |
| --- | --- |
| `trader` CLI | `kairos` |
| `trading` 包 | `kairos` |
| `MockScenario` | `SyntheticScenario` |
| `make_mock_dataset` | `build_synthetic_backtest_dataset` |
| 裸 `Adapter` 类名 | `Gateway` / `Client` / `Port` / `Normalizer` |
| `day_aggs` 公共命名 | `daily_ohlcv` |

## 批量改名顺序建议

推荐顺序如下：

```text
Wave 0 命名冻结
  -> Wave 1 Kairos 品牌和 CLI
  -> Wave 2 mock/fixture 收敛
  -> Wave 3 OHLCV/Bar 收敛
  -> Wave 4 adapter 职责拆分
  -> Wave 5 research/studies 边界
  -> Wave 6 trading -> kairos 包名迁移
  -> Wave 7 删除旧名
```

不建议把 Wave 4 和 Wave 6 合并。`adapter` 拆分会改很多业务导入，包名迁移也会改所有导入；两个一起做会让代码审查和回归定位非常痛苦。

## 本项目当前做得好的命名

以下命名建议保留，并作为后续改名的参照：

- `InstrumentId` / `InstrumentDefinition` / `ListingDefinition`：明确区分内部标的身份和 venue 挂牌。
- `ReferenceCatalog` 与 `DataCatalog`：很好地区分产品事实和数据发布事实。
- `DatasetRelease`、`QualityLevel`、`DatasetStatus`：符合可复现研究和数据治理语义。
- `StrategyRelease`、`RunArtifact`：适合审计和策略晋级流程。
- `Intent`、`ExecutionPlan`、`OrderRequest`、`TradeExecution`、`LedgerTransaction`：基本符合专业交易系统流水线。
- `ReconciliationService`、`KillSwitch`、`RiskLimits`：生产交易系统中常见且清晰。

## 命名验收清单

以后新增模块、类、CLI 或数据产品时，建议用下面的问题检查：

1. 这个名字表达的是经济身份、数据身份、运行实例，还是技术实现？
2. 名字里是否把 provider、venue、symbol、instrument 混在一起？
3. 名字是否能在没有上下文时让量化研究员、执行工程师和风控都理解？
4. 如果明年换数据供应商，这个名字是否还成立？
5. 如果从教程升级到生产，名字是否仍然严肃可信？
6. `mock`、`fixture`、`synthetic` 是否只出现在测试/验收边界内？
7. CLI 命令是否先表达动作和对象，再通过参数选择策略、数据、provider？
8. 如果名字里出现 `adapter`、`service`、`manager`、`handler`、`models`、`base`，是否能改成更具体的职责名？
9. 如果是行情聚合数据，命名是否正确区分了 `OHLCV` schema 和 `Bar` 代码对象？

## 建议优先处理清单

最值得先做的五件事：

1. 将产品、README 标题、CLI 目标名定为 `Kairos` / `kairos`，保留 `trader` 兼容入口。
2. 清理并确认不再出现提交态 `__pycache__`、`.pyc`、`pyenv/`。
3. `trading/backtest/mock.py` 改名为 `synthetic_scenarios.py`，并保留兼容导入。
4. 把 `adapter` 命名按职责拆成 `ExecutionGateway`、`MarketDataClient`、`ReferenceDataClient`、`EventNormalizer`、`Port`。
5. CLI 的 `paper-sma`、`shadow-sma`、`simulate-sma` 增加通用入口。
6. `ResearchSpec` 改为更具体的期权链/标的选择 spec，避免通用研究规格被 SPXW 默认值占用。
