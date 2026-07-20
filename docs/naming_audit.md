# 项目命名专业度审计与改名建议

本文基于当前工作区的目录、公开 API、CLI、示例、研究脚本和文档命名做一次命名审计。目标不是追求“更好听”，而是让命名更接近专业量化团队常用的语义：身份稳定、层次清晰、研究与生产边界明确、供应商/交易场所/经济产品不混用。

## 总体结论

项目的核心领域命名已经有较好的专业基础，尤其是 `InstrumentDefinition`、`ListingDefinition`、`ReferenceCatalog`、`DatasetRelease`、`QualityLevel`、`Intent`、`ExecutionPlan`、`Ledger Transaction`、`Portfolio / Risk View` 这一组概念，基本符合机构级量化系统的分层方式。

如果产品名确定为 `kairos`，建议把它作为产品品牌和包命名空间的核心，而不是再使用泛泛的 `trader`。`Kairos` 本身有“关键时机 / 时机判断”的含义，和量化交易中择时、信号、执行窗口、风险门禁都比较贴合，适合作为平台名。

主要问题集中在四类：

1. 项目顶层名称偏泛：`trader` / `trading` 容易被理解成普通交易脚本，而不是研究、数据、执行和账户事实系统；建议统一升级为 `kairos`。
2. 研究与正式系统曾混在相近命名中：顶层源码工作区已迁为 `studies/`；包内平台研究服务统一为 `study_platform` 入口；期权链采集规格已改为 `OptionChainCaptureSpec`，旧 `kairos.research` 包已删除，`ResearchSpec` 已删除。
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
| Python 包 | `kairos` | 唯一真实包名；短、稳定、不泛 |
| CLI | `kairos` | 唯一命令入口；例如 `kairos data search`、`kairos run backtest` |
| 项目发布名 | `kairospy` | PyPI 分发名；安装命令使用 `pip install kairospy`，但 import 包名和 CLI 继续使用 `kairos` |
| 兼容别名 | 不保留 | 避免新用户误以为存在第二个项目或第二套 API |

`kairos` 是唯一产品名、Python import 包名和 CLI 名；PyPI 分发名使用 `kairospy`。旧 `trading` / `trader` 不作为最终兼容面保留：

| 名称 | 当前角色 | 目标角色 |
| --- | --- | --- |
| `Kairos` | 产品品牌 | 对外统一项目名，出现在 README、文档标题、发布说明和产品叙述中 |
| `kairos` | CLI / Python 包 / 公开 API | 用户、研究员、Notebook、示例和内部实现统一使用的唯一入口 |
| `trading` | 旧内部 Python 包名 | 已迁移为 `kairos/`，不进入打包配置，不保留目录 |
| `trader` | 旧 CLI / 旧发布名 | 已从 `pyproject.toml` scripts 中移除 |

当前架构采用“同一项目，单入口”的方式：

1. 对外统一称为 `Kairos`。
2. 新命令统一使用 `kairos ...`。
3. 新公开导入优先使用 `from kairos import ...` 或 `from kairos.xxx import ...`。
4. 打包配置只包含 `kairos*`，并精确排除旧 `kairos.research` / `kairos.research.*` 与顶层 `research` / `studies` 工作区。
5. 旧 `trader` console script 不再发布。

当前已落地进展：真实实现包已从旧目录迁移到 `kairos/`；公开 Python facade 只导出 `Kairos`，不再导出 `Trader`；README、用户指南、examples 和 Notebook 示例已统一改用 `kairos` 命名空间。

当前收口状态：`research` 作为“用户工作区目录/旧包名”已经不进入最终安装包，包内平台主入口已迁为 `kairos.study_platform`，通用数据读取主名已迁为 `DatasetClient`，Study run mode 主名已迁为 `RunMode.STUDY` / `study_composition`，Strategy lifecycle 主名也已迁为 `STUDY_VALIDATED`。`ResearchDataClient` 公开导出、`RunMode.RESEARCH`、`research_composition`、顶层 `kairos research` CLI、`--mode research` 公开 choice、IBKR `ResearchProvider` 模块名、`QualityLevel.RESEARCH`、`DatasetStatus.APPROVED_FOR_RESEARCH` 和 `@research`/`@latest-research` 数据 alias 已经移除；用户文档文件名和正文里的英文 Research 主术语也已迁为 Study。剩余 `research` 仅作为打包排除规则和负向回归测试中的旧入口拒绝样例出现。

不建议使用 `kairos_trading` 作为主包名，因为它又把系统缩回“交易”一层；当前项目已经覆盖 research、data、pricing、risk、treasury、execution、ledger，`kairos` 单独作为平台命名更干净。

建议的文档标题从：

```text
# Kairos
```

改为：

```text
# Kairos
```

旧 CLI 示例从：

```text
kairos run backtest --strategy sma-cross-v1@1.2.0
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

1. 目录已从 `adapters/` 迁移为 `connectors/`；具体模块继续按职责使用 `gateway`、`client`、`provider`、`decoder`、`ingestion`。
2. 类名不要只叫 `XxxAdapter`；必须说清楚它是 `Client`、`Gateway`、`Normalizer`、`Ingestor`、`Repository` 还是 `Port`。
3. `Gateway` 用于会产生外部副作用的边界，例如下单、撤单、转账。
4. `Client` 用于读取外部 API 或下载文件。
5. `Normalizer` / `Decoder` 用于供应商格式到 canonical schema 的转换。
6. `Port` 用于内部协议，不绑定具体供应商。
7. `Connector` 可以作为目录或组合对象名，但最好也带职责：`MarketDataConnector`、`ExecutionConnector`。

推荐迁移方向：

```text
kairos/connectors/binance/adapter.py
-> kairos/connectors/binance/execution_gateway.py
-> kairos/connectors/binance/account_gateway.py
-> kairos/connectors/binance/market_data_client.py
-> kairos/connectors/binance/reference_data_client.py

kairos/connectors/massive/day_aggs.py
-> kairos/connectors/massive/daily_ohlcv_client.py
-> kairos/connectors/massive/daily_ohlcv_ingestor.py

kairos/ports/venue.py
-> kairos/ports/venue.py
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
| `trader` | 旧 CLI script | 偏个人工具名，不体现系统是量化研究与交易基础设施 | 已移除；只发布 `kairos` console script | 已落地 |
| `Trader` | Python facade 类名、Notebook 示例 | 会让 API 看起来仍有旧产品名 | 已移除包根导出和 API 别名；新代码统一使用 `from kairos import Kairos` | 已落地 |
| `trading` | 旧顶层 Python 包 | 过泛，容易和下单执行层混淆；当前项目实际包含 data、research、risk、treasury、execution | 已迁移为真实 `kairos/` 包；打包配置不再包含 `trading*` | 已落地 |
| 用户文档和 examples 中的 `from kairos...` | README、docs、examples、Notebook 示例 | pip install 后用户应以 `kairos` 作为唯一公开入口；继续复制 `trading` 会把内部兼容包暴露成产品主入口 | 已落地；README、数据指南、examples 和 Notebook 示例统一改为 `from kairos...`，仓库 hygiene 新增回归护栏 |
| `studies/` 与 `kairos/study_platform` | 顶层研究脚本和包内研究服务 | 顶层研究工作区已改为 `studies/`；可打包平台服务统一为 `kairos.study_platform` | 新代码统一使用 `kairos.study_platform`；旧 `kairos.research` 包已删除且被打包配置排除 | 已落地 |
| `ResearchSpec` | 旧 `kairos/research/spec.py` | 已改为 `OptionChainCaptureSpec`；旧名已删除 | 新代码统一使用 `OptionChainCaptureSpec` / `kairos.study_platform.spec` | 已落地 |
| `ResearchSpec` 包根导出 | 旧 `kairos.research`、`kairos.study_platform` | 包根导出旧名会让 Notebook 和示例继续发现旧 facade | 已移除包根 re-export；旧名已从 `kairos.study_platform.spec` 删除 |
| `service/analyzer/selector` | 旧 `kairos.research` 模块 | 新平台 namespace 不应继续挂旧笼统模块名，否则用户自动补全会发现旧入口 | 已落地；`kairos.study_platform` 只挂 `option_capture`、`option_snapshot_analysis`、`option_universe_selector` 等正式模块 |
| `kairos.research` | `kairos` 顶层 namespace | `research` 是旧兼容包名，和新用户的研究平台入口 `study_platform` 并存会产生歧义；且不应进入最终 wheel | 已落地；源码导入迁移到 `kairos.study_platform`，旧目录删除，`pyproject.toml` 显式排除 `kairos.research` 和 `kairos.research.*` |
| `connectors.massive` 包根 | `kairos.connectors.massive` / `kairos.connectors.massive` | 旧 facade 指向 `kairos.adapters.massive` 会把 `DayAgg`、`DayIv`、`Readiness` 等旧兼容名泄漏给新用户 | 已落地；`kairos.connectors.massive` 已成为真实实现包根，只导出 `DailyOhlcv`、`CloseImpliedVolatility`、`EntitlementDiagnostics` 等正式名 |
| `ResearchService.capture` / `kairos research capture` | 旧研究采集 API 与 CLI 入口 | 已改为 `OptionCaptureService.capture_snapshot` 与 `kairos study capture`；真实实现位于 `kairos/study_platform/option_capture.py`；旧类名、旧方法、旧 `service.py` 和顶层 `research` 命令组已删除 | 后续如果加入非期权采集，应按资产/数据对象拆成明确入口，不恢复 `ResearchService` 泛名 | 已落地 |
| `ResearchDataClient` | `kairos.data` 公开数据读取 API、README、数据指南、测试 | 旧名把 Data Product 的通用查询能力限定成 research；在 pip install 后用户做 backtest/paper/live 项目时语义不够直观 | 已改为 `DatasetClient`；公开导出、测试主路径和文档主路径均已移除旧名 | 已落地 |
| `RunMode.RESEARCH` / `research_composition` | Run Product 与应用层运行模式 | 用户工作流目标已经是 Study Product；`research` 更像活动描述，不像稳定产品状态 | 已改为 `RunMode.STUDY` / `study_composition`；`kairos run start --mode study` 是唯一公开路径；`RunMode.RESEARCH`、`research_composition` 和旧 `--mode research` 公开 choice 已删除 | 已落地 |
| `kairos.research_platform` | 包内可打包研究平台服务 | 目录不再是顶层用户 `research` 工作区，但旧包名仍把 Study/Factor/Validation 旧称为 research platform | 已迁移为真实包 `kairos.study_platform`；仓库内导入和文档主路径已切到 `kairos.study_platform`，旧 `kairos/research_platform` 目录不保留 | 已落地 |
| `RESEARCH_VALIDATED` / `research-default` | Strategy lifecycle、Data promotion policy | 治理状态中使用 research 作为晋级阶段名会和 Study Product 命名冲突 | 已改为 `STUDY_VALIDATED` / `study-default`；旧 `RESEARCH_VALIDATED` enum alias、CLI choice 和旧 `research-default` profile 已删除；promotion evidence bundle 字段从 `research_result_paths` 收敛为 `evidence_paths` | 已落地 |
| `QualityLevel.RESEARCH` / `APPROVED_FOR_RESEARCH` / `@research` | Data Contract、Catalog alias、Data Product release gates | Q2 数据等级仍使用 research 会让 Study/Backtest/Production 数据治理和产品命名不一致 | 已改为 `QualityLevel.STUDY`、`DatasetStatus.APPROVED_FOR_STUDY`、`approved_for_study`、`@study` / `@latest-study`；Q2 质量编码保留 | 已落地 |
| `kairos/backtest/mock.py` | 回测数据生成 | 旧名会把测试替身语义误导为正式数据产品 | 已删除；真实入口为 `kairos/backtest/synthetic_scenarios.py` | 已落地 |
| `make_mock_dataset` | 旧回测数据生成函数 | 旧名不说明数据是 synthetic scenario，也不适合作为公共 API | 已删除；使用 `build_synthetic_backtest_dataset` | 已落地 |
| `paper-sma`、`shadow-sma`、`simulate-sma`、`backtest-sma` | CLI 和文档 | 策略名不应固化在运行模式命令里，否则平台入口会被示例策略污染 | 已删除旧 handler 和产品文档入口；统一使用 `kairos run <mode> --strategy ...` | 已落地 |
| `trade run` | 旧策略运行/人工订单混合入口 | `trade` 既像产品名又像动作桶，且混合人工订单和运行验收职责 | 已删除；人工订单使用 `kairos order submit`，外部运行验收使用 `kairos runtime soak` | 已落地 |
| `sma_strategy.py` 与 `sma_cross.py` | `kairos/strategies/`，未来 `kairos/strategies/` | 已改为 `sma_cross_strategy.py`（正式策略运行时）和 `sma_cross_study_backtest.py`（Study/批量回测）；旧文件已删除 | 已落地 |
| `main.py` | 仓库根目录 | 已删除根目录转发入口；统一使用 `kairos` / `python -m kairos`，不再保留旧 `trading` 包入口 | 已落地 |

## 中优先级改名建议

| 当前命名 | 位置/范围 | 问题 | 建议命名 |
| --- | --- | --- | --- |
| `massive` | `kairos/connectors/massive/*`、数据 CLI、DatasetKey lineage | 供应商实现层需要保留 vendor 名；用户侧命令不应为每个 provider 发明一组动词 | 已落地；公开数据命令使用 `provider-* --provider massive`，`kairos.connectors.massive` 只作为真实 connector 实现入口，DatasetKey 中的 `provider=massive` 保留为 lineage |
| `day_aggs` | Massive 适配器、CLI、数据集 | vendor API 叫法，行业里更常见 `daily_bars` 或 `daily_ohlcv`；数据产品名更建议 `ohlcv` | `daily_ohlcv` 用于数据产品；`DailyBar` / `BarSeries` 用于代码对象 |
| `option_day_iv` | 旧 Massive IV 物化模块和 CLI | 已新增 `OptionCloseImpliedVolatilityPipeline`、`close_implied_volatility.py` 和 `prepare-option-close-implied-volatility` 主路径；旧 `option_iv.py`、`OptionDayIvPipeline` 与 `prepare-option-day-iv` 已删除 | 已落地；新代码统一使用 close-implied-volatility 命名 |
| `market_slice` | `kairos/data/market_slice_*`、`kairos/backtest/feed.py` | 已改为 `MarketSnapshot`、`MarketSnapshotFeed`、`MarketSnapshotStorageDriver`、`MarketSnapshotCollectionPublisher`；旧 `MarketSlice*` 兼容别名已删除 | 已落地；新代码统一使用 market snapshot 语义 |
| `HistoricalDataset` | `kairos/backtest/feed.py` | 已改为 `MarketReplayDataset`；旧名已删除 | 已落地；新代码统一使用 `MarketReplayDataset` |
| `register_historical_dataset` / `historical_dataset.v2` | `kairos/data/publishing.py`、Release metadata | 已新增 `register_market_replay_dataset` 主入口和 `market_replay_dataset.v2` 新 schema id；旧函数名已删除；旧 schema 仅用于历史数据识别 | 已落地；新发布流程统一使用 MarketReplayDataset 语义 |
| `ContractMetadata` | `kairos/backtest/feed.py` | 已改为 `InstrumentLifecycleSnapshot`；旧名已删除 | 已落地；新代码统一使用 `InstrumentLifecycleSnapshot` |
| `DatasetProduct` | `kairos/data/models.py` | 已新增真实类名 `DataProductDefinition`，并导出 `DataProduct` 短别名；真实定义已迁到 `kairos.data.contracts`，`DatasetProduct` 已删除 | 已落地；新文档、新测试和数据实现已改用 `DataProductDefinition` / `DataProduct`；`kairos.data` 包根不再主动 re-export 旧名 |
| `DatasetProductSpec` | `kairos/data/models.py` | 已改为 `DataProductContract`，表达一个数据产品的 schema、storage、quality 和 usage contract；真实定义已迁到 `kairos.data.contracts`，旧名已删除 | 已落地；新代码统一使用 `DataProductContract` |
| `ProductSpec` | `kairos/domain/product.py` | 已新增 `InstrumentContractSpec` 作为经济合约规格主名；旧 `ProductSpec` 已删除 | 已落地；新文档统一使用 `InstrumentContractSpec` |
| `RunMode.LIVE_PAPER` | `kairos/data/models.py` | `RunMode` 真实定义已迁到 `kairos.data.contracts`；已新增 `RunMode.PAPER_TRADING = "paper-trading"` 和 `paper_trading_composition` 主入口；旧 `LIVE_PAPER` / `live_paper_composition` 已删除 | 已落地；新代码统一使用 `PAPER_TRADING` / `paper-trading` |
| `application/runtime_golden.py` | 应用层 | 已改为 `runtime_reference_artifact.py` 与 `run_runtime_reference_artifact`；旧模块、旧函数和隐藏 CLI alias 已删除 | 已落地；CLI 使用 `runtime reference-artifact` |
| `runtime_failure_matrix.py` | 应用层 | 已改为 `runtime_failure_policy.py` 与 `run_runtime_failure_policy`；旧模块、旧函数和隐藏 CLI alias 已删除 | 已落地；CLI 使用 `runtime failure-policy` |
| `task_supervisor.py` | 应用层 | 已改为 `service_supervisor.py`、`AsyncServiceSupervisor`、`ManagedServiceSpec`、`ManagedServiceStatus`、`ManagedServiceSnapshot`、`ServiceCriticality`、`ServiceFault`；旧模块和旧 `*Task*` 类型已删除 | 已落地；新代码统一使用 managed service 语义 |

## 低优先级但建议统一的命名

| 当前命名 | 问题 | 建议 |
| --- | --- | --- |
| `adapter.py` 在多个供应商目录中重复 | IDE 标签页和搜索结果不直观，且 `adapter` 不说明职责 | `execution_gateway.py`、`market_data_client.py`、`event_normalizer.py`、`reference_data.py` |
| `composite.py` | 组合什么不明确；组合行情路由已改为 `market_data_router.py`，旧 `kairos.adapters.composite` 已删除 | `market_data_router.py` / `CompositeMarketDataClient` |
| `binance.adapter` 中的 market stream 工具 | public WebSocket URL、stream session 和 market event parser 与交易/账户网关混在同一入口 | 已新增 `binance.market_stream` / `kairos.connectors.binance.market_stream` 主入口；新代码统一从该模块导入 `BinanceStreamSession`、`WebSocketClientConnector`、`websocket_url`、`parse_market_stream_event` |
| `binance.adapter` 中的 REST transport / signing | transport、rate limiter、request signer 和 clock sync 属于基础设施，不是交易 adapter | 已新增 `binance.rest_transport` 与 `binance.request_signing`；新代码统一从这两个模块导入 `BinanceTransport`、`UrllibBinanceTransport`、`RateLimiter`、`BinanceSigner`、`synchronize_clock` |
| `binance.adapter` 中的 reference data client | reference data discovery 与 execution/account gateway 混在一起，边界不直观 | 已新增 `binance.reference_data`；新代码统一从该模块导入 `BinanceSpotReferenceDataClient`、`BinanceFuturesReferenceDataClient`、`BinanceOptionsReferenceDataClient` |
| `binance.adapter` 中的 market data client | REST snapshot 行情读取不应和 execution/account gateway 混在一起 | 已新增 `binance.market_data_client`；新代码统一从该模块导入 `BinanceMarketDataClient` |
| `binance.adapter` 中的 execution gateway | 下单、撤单、杠杆/保证金设置和订单恢复属于会产生交易副作用的 execution boundary，不应藏在通用 adapter 文件 | 已新增 `binance.execution_gateway` / `kairos.connectors.binance.execution_gateway`；新代码统一从该模块导入 `BinanceExecutionGateway`、`BinanceOptionsExecutionGateway` 与 execution capabilities |
| `binance.adapter` 中的 account gateway | 账户、余额、仓位和挂单读取是账户边界，不应和 execution/market data 混在同一个 adapter 文件 | 已新增 `binance.account_gateway` / `kairos.connectors.binance.account_gateway`；新代码统一从该模块导入 `BinanceAccountGateway`、`BinanceOptionsAccountGateway` |
| `binance.adapter` 中的 funding settlement client | 资金费率结算流水是 post-trade/funding settlement 数据，不属于通用交易 adapter | 已新增 `binance.funding_settlement` / `kairos.connectors.binance.funding_settlement`；新代码统一从该模块导入 `BinanceFundingSettlementClient` |
| `binance.adapter` 中的 user data stream | listen key 生命周期、私有成交事件和余额事件属于 user data stream 边界 | 已新增 `binance.user_data_stream` / `kairos.connectors.binance.user_data_stream`；新代码统一从该模块导入 `BinanceUserDataStreamService`、`BinanceUserStreamProcessor`、`UserFillUpdate` |
| `binance.adapter` 中的 option market snapshot parser | 期权 bid/ask/mark/greeks 快照是市场数据归一化，不是账户或执行 adapter | 已新增 `binance.option_market_snapshot` / `kairos.connectors.binance.option_market_snapshot`；新代码统一从该模块导入 `OptionMarketSnapshot`、`parse_option_market_snapshot` |
| `binance.adapter` 中的 order recovery coordinator | REST backfill、open order 查询和私有成交去重组合成恢复协调服务，应独立于通用 adapter | 已新增 `binance.order_recovery` / `kairos.connectors.binance.order_recovery`；新代码统一从该模块导入 `BinanceRecoveryService`、`RecoverySnapshot` |
| `ibkr.adapter` 中的 session | TWS/Gateway 连接生命周期属于连接会话，不是交易/行情 adapter | 已新增 `ibkr.session` / `kairos.connectors.ibkr.session`；新代码统一从该模块导入 `IbkrSession` |
| `ibkr.adapter` 中的 account gateway | IBKR 账户摘要、现金余额、仓位读取属于账户边界 | 已新增 `ibkr.account_gateway` / `kairos.connectors.ibkr.account_gateway`；新代码统一从该模块导入 `IbkrAccountGateway` |
| `ibkr.adapter` 中的 reference data client | 股票、ETF、上市期权合约发现和 contract binding 属于 reference data discovery | 已新增 `ibkr.reference_data` / `kairos.connectors.ibkr.reference_data`；新代码统一从该模块导入 `IbkrReferenceDataClient` |
| `ibkr.adapter` 中的 market data client | quote、recent trade、historical bar 读取属于只读行情客户端 | 已新增 `ibkr.market_data_client` / `kairos.connectors.ibkr.market_data_client`；新代码统一从该模块导入 `IbkrMarketDataClient` |
| `ibkr.adapter` 中的 execution gateway | 下单、撤单、combo order 和订单恢复属于 execution boundary | 已新增 `ibkr.execution_gateway` / `kairos.connectors.ibkr.execution_gateway`；新代码统一从该模块导入 `IbkrExecutionGateway`、`normalize_ibkr_execution` |
| `transfer` 包级公开入口中的 `*TransferAdapter` | 资金划转是明确的副作用边界，应使用 `TransferGateway`；旧 Adapter 名已删除 | `kairos.connectors.transfer` 包入口已只导出 `BinanceTransferGateway`、`BankTransferGateway` 等新名 |
| Deribit connector | 当前真实类名已使用 `DeribitDvolProvider`、`DeribitOptionChainProvider`、`DeribitOptionTradeHistoryProvider` | 暂不需要改类名；后续只需统一文档里的泛称 |
| `data write --live --adapter` | 用户数据产品入口 | 已改为 `--connector`；旧 `--adapter` 隐藏兼容参数和内部 `args.adapter` 读取已删除 | 已落地；manifest 使用 `connector_hash` |
| execution/account runtime boundary 局部变量和错误文案 | `ExecutionRouter`、`RuntimeRecoveryService`、`ReconciliationService`、`KillSwitch`、CLI trade runtime | 运行期真实边界是下单、撤单、账户读取和恢复，属于 `Gateway`，不应继续用 `adapter` 描述 | 已落地；核心属性和报错已改为 `gateway` / `connector`，卫生测试防止 `account_adapter` 和旧 adapter 报错文案回流 |
| `ValuationService` / `TreasuryService` 包根导出 | `kairos.pricing`、`kairos.treasury` | 名称过泛，容易和具体 valuation/posting 职责混淆 | 已删除；使用 `OptionValuationService` / `TreasuryLedgerPostingService` |
| `base.py` | Python 项目常见，但公共领域语义弱；`kairos/strategies/base.py` 已改为 `strategy_protocols.py`，旧文件已删除 | `protocols.py`、`interfaces.py`、`ports.py` |
| `service.py` | 过泛，多个包都有 `service.py`；`kairos/pricing/service.py` 已改为 `option_valuation.py`，旧 study capture service 已改为 `option_capture.py`，`kairos/treasury/service.py` 已改为 `ledger_posting.py`，旧文件已删除 | 用业务动作命名，如 `dataset_publication.py`、`option_valuation.py`、`option_capture.py` |
| `models.py` | 过泛，但在小包内可接受；`kairos/data/models.py` 已改为 `contracts.py`，`kairos/reference/models.py` 已改为 `contracts.py`，`kairos/volatility/models.py` 已改为 `contracts.py`，`kairos/study_platform/validation/models.py` 已改为 `contracts.py`，`kairos/treasury/models.py` 和 `transfer_models.py` 已改为 `transfer_contracts.py`，`kairos/pricing/models.py` 和 `option_pricing_models.py` 已改为 `option_pricing_contracts.py`，旧文件已删除 | 后续如继续细拆 Reference，可从 `contracts.py` 拆出 `instrument_definitions.py`、`listing_definitions.py`、`routing_definitions.py` |
| `analyzer.py` | 分析对象不明确；旧 analyzer 已删除，当前主类型为 `OptionSnapshotAnalysis` / `OptionSnapshotMetricRow` | `option_snapshot_analysis.py`、`study_evidence_analysis.py` |
| `selector.py` | 选择什么不明确；旧 `kairos/research/selector.py` 已删除，当前期权入口为 `option_universe_selector.py` | `option_universe_selector.py` 或 `instrument_selector.py` |
| `readiness.py` / `health.py` / `doctor` | 三者边界接近；`kairos/data/health.py`、`DataHealth*` 和 `kairos data health` 已删除，数据质量主入口为 `kairos/data/diagnostics.py`、`DataDiagnosticsService` / `DataDiagnosticIssue` 和 `kairos data diagnostics`；Massive 权限探测已改为 `entitlement_diagnostics.py`，主类为 `MassiveEntitlementDiagnostics` / `MassiveEntitlementReport`，旧 `readiness.py`、`MassiveReadiness*` 和 `massive-readiness` 已删除；US equity momentum 数据包审计已改为 `us_equity_momentum_diagnostics.py`、`UsEquityMomentumDiagnostics` 和 `us-equity-momentum-diagnostics`，旧 readiness 模块/类/命令已删除 | 统一命名层次：内部 API 使用 `diagnostics`；供应商权限探测使用 `entitlement-diagnostics`；准备度证据字段可继续叫 readiness，但文件/主命令不要泛称 readiness |
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
| 逻辑数据产品 | `DataProductDefinition` / `DataProduct` | 已选定；`DatasetProduct` 已删除 |
| 数据产品完整契约 | `DataProductContract` | 已选定；`DatasetProductSpec` 已删除 |
| 不可变发布 | `DatasetRelease` | 推荐保留 |
| 浮动别名 | `ReleaseAlias` | 比单独 `Alias` 更明确 |
| 原始供应商归档 | `VendorArchiveClient` | 已选定 `MassiveVendorArchiveClient`；旧 `MassiveSourceArchive` 已删除 |
| 标准化事件 | `CanonicalMarketEvent` | 推荐保留 canonical 语义 |
| 点时数据 | `PointInTimeDataset` | 强调 `available_time` |
| 数据质量门禁 | `DataQualityGate`、`QualityProfile` | 推荐保留 |

### 研究与策略

| 概念 | 推荐英文命名 | 说明 |
| --- | --- | --- |
| 研究课题 | `Study` | 不等于 `Strategy` |
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
2. 确立 `Kairos` / `kairos` 为同一项目的产品、公开包和 CLI 目标命名，移除 `trader` / `trading` 兼容别名。
3. 清理本地运行产物：`__pycache__`、`.pyc`、`.pytest_cache`、`pyenv/` 不应进入版本库或审计范围。
4. 高风险旧名不再新增 alias；`MockScenario`、`make_mock_dataset` 这类旧入口已删除。
5. CLI 已删除旧策略专用运行命令，统一使用 `run shadow --strategy ...`、`run paper --strategy ...`、`run simulate --strategy ...` 和 `run backtest --strategy ...`。
6. 明确 `OHLCV` 用于数据产品/schema，`Bar` 用于代码对象和市场事件。

### 第二阶段：公共 API 语义收敛

1. 顶层源码研究工作区已从 `research/` 改为 `studies/`。
2. 已将 `ResearchSpec` 收敛为更具体的 `OptionChainCaptureSpec`，旧名已删除。
3. 将 `kairos/backtest/mock.py` 改为 `kairos/backtest/synthetic_scenarios.py`。
4. 将 `adapter` 类名按职责改为 `Client`、`Gateway`、`Normalizer`、`Ingestor`、`Port`。
5. 已将 `MarketReplayDataset` 内部单点行情对象从 `MarketSlice` 收敛为 `MarketSnapshot`，旧名已删除。

### 第三阶段：平台级命名升级

1. 将 Python 包从 `trading` 迁移到 `kairos`。
2. 将供应商名从 CLI 主路径中下沉到 `--provider massive` 这类参数。
3. 统一 DatasetKey 格式，避免 `provider` 出现在过高层级。例如优先使用 `market.ohlcv.equity.us.1d.raw` + `provider=massive` 维度，而不是把 `massive` 固定在逻辑产品名中。
4. 目录已迁移为 `connectors/`，协议层已迁到 `ports/`，副作用边界继续使用 `*Gateway`，只读外部 API 使用 `*Client` 或 `*Provider`。

## 批量调整方案

建议按 7 个批次推进。原则是：先加新名和兼容层，再逐步迁移调用方，最后删除旧名。不要一次性把包名、CLI、目录、类名、数据集 ID 全部改掉，否则测试失败时很难定位。

### Wave 0：命名冻结与自动检查

目标：先阻止新旧命名继续扩散。

典型改动：

| 动作 | 内容 |
| --- | --- |
| 建立命名决策记录 | 在 `docs/` 增加或链接本文，明确 `Kairos`、`OHLCV/Bar`、`Gateway/Client/Port` 规则 |
| 增加禁用词扫描 | 在 hygiene test 中扫描新增文件名和公开类名，提示 `adapter`、`manager`、`utils`、裸 `service`、裸 `models` |
| 建立迁移策略 | 迁移期先加新名再删除旧名；当前最终态不保留旧项目名、旧 CLI 或旧 adapter 参数 |

验收标准：

1. 新文档通过评审。
2. 新增命名不得继续引入裸 `adapter`、`utils`、`manager`。
3. 测试不要求大规模改名，但能提醒后续新增命名。

### Wave 1：品牌和 CLI 迁移

目标：把产品名切到 `Kairos`，并在最终态删除旧 CLI。

典型改动：

| 当前 | 新命名 | 最终状态 |
| --- | --- | --- |
| README `# Kairos` | `# Kairos` | README 不再展示旧 CLI 别名 |
| `pyproject.toml` script `trader` | 增加 script `kairos` | 删除 `trader` script，只保留 `kairos` |
| 文档命令 `trader ...` | 新文档统一 `kairos ...` | 旧命令已删除，不再作为兼容说明展示 |

验收标准：

1. `kairos --help` 是唯一 CLI 帮助入口。
2. README 首屏使用 `Kairos`。
3. CI 和现有示例不因 CLI 改名失败。

### Wave 2：fixture/mock 命名收敛

目标：让测试/教学数据不再看起来像正式研究证据。

典型改动：

| 当前 | 新命名 | 兼容方式 |
| --- | --- | --- |
| `kairos/backtest/mock.py` | `kairos/backtest/synthetic_scenarios.py` | 旧文件已删除 |
| `MockScenario` | `SyntheticScenario` | 旧别名已删除 |
| `make_mock_dataset` | `build_synthetic_backtest_dataset` | 旧函数已删除 |
| CLI `backtest mock` | `backtest synthetic-scenario` | 旧命令已删除 |
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
| `prepare-massive-equity-day-aggs` | `prepare-equity-daily-ohlcv --provider massive` | 旧命令已删除 |
| `Bar` / `BarSeries` | 保留 | 代码对象命名专业且直观 |
| quote 聚合 bar | `QuoteBar` / `MidQuoteBar` | 避免误认为成交 OHLCV |

验收标准：

1. DatasetKey 新增命名统一为 `market.ohlcv...`。
2. 代码里 `Bar` 类型不被机械改成 `OHLCV`。
3. 供应商术语 `day_aggs` 只允许出现在外部供应商原始 key/provenance 中，例如 `day_aggs_v1`。

### Wave 4：adapter 拆成职责命名

目标：把 `adapter` 从公共类名和核心路径中降级，改成更具体的边界职责。

典型改动：

| 当前 | 新命名 | 用途 |
| --- | --- | --- |
| `adapters/base.py` | `ports/venue.py`、`ports/execution.py`、`ports/market_data.py`、`ports/reference_data.py` | 内部协议；旧文件已删除 |
| `BinanceExecutionAdapter` | `BinanceExecutionGateway` | 下单/撤单等外部副作用 |
| `BinanceAccountAdapter` | `BinanceAccountGateway` | 账户、余额、仓位读取 |
| `BinanceMarketDataAdapter` | `BinanceMarketDataClient` | 行情读取 |
| `IbkrReferenceAdapter` | `IbkrReferenceDataClient` | 合约和参考数据 |
| `MassiveSourceArchive` | `MassiveVendorArchiveClient` | 供应商原始响应归档读取；旧名已删除 |
| decoder 类 | `EventDecoder` / `MarketDataNormalizer` | 原始响应到 canonical |

迁移策略：

1. 先新增新类名，旧类名继承或别名到新类。
2. 更新内部导入到新名。
3. 删除旧名，最终公开 API 不再暴露裸 `Adapter`。

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
| 顶层 `research/` | `studies/` | 已迁移；表示具体研究项目、实验、报告 |
| `kairos/research/` | `kairos/study_platform/` / `kairos.study_platform` | 已迁移；旧包已删除且不进入打包配置 |
| `ResearchSpec` | `OptionChainCaptureSpec` | 已落地；避免通用名绑定 SPXW 期权链采集 |
| `ResearchService` | `OptionCaptureService` | 已落地；旧名与顶层 `kairos research` CLI 均已删除 |
| `research.analyzer` / `ResearchResult` | `option_snapshot_analysis` / `OptionSnapshotAnalysis` | 已落地；旧模块、旧类名和 `analyze` 别名已删除 |

验收标准：

1. 顶层研究项目与包内研究服务不再同名。
2. SPXW/IBKR 相关默认值不再藏在通用 `ResearchSpec` 中，新代码使用 `OptionChainCaptureSpec`。
3. README 对 `Study`、`Factor`、`Strategy` 的边界更清楚。

### Wave 6：包名从 `trading` 迁移到 `kairos`

目标：完成平台命名升级。这里不是新建第二个项目，而是把同一项目的公开命名空间从旧实现名 `trading` 收敛到产品名 `kairos`。

典型改动：

| 当前 | 新命名 | 兼容方式 |
| --- | --- | --- |
| `kairos/` | `kairos/` | 真实实现包直接位于 `kairos`，不保留 `trading` 目录 |
| `from kairos...` | `from kairos...` | 内部导入全部迁移 |
| `python -m kairos` | `python -m kairos` | 正式模块入口 |
| package include `kairos*` | `kairos*` | 只打包 Kairos 产品库，并精确排除源码研究工作区 |

验收标准：

1. 内部源码不再从 `trading` 导入。
2. `python -m kairos --help` 正常。
3. `kairos` console script 是唯一发布的 CLI。
4. 测试、示例、文档都优先使用 `kairos`。

推荐执行顺序：

1. 直接将真实实现包迁移到 `kairos`，保证 `python -m kairos` 与 `kairos` CLI 可用。
2. 再把 README、docs、examples、Notebook 示例和测试命令迁移到 `kairos`。
3. 然后按子包逐步迁移内部导入：先无副作用的 domain/data/models，再迁移 application/runtime/connector。
4. 删除旧 `trading` 包路径，打包配置只包含 `kairos*`。
5. 删除旧名只能放在 release notes 公告后的下一个迁移周期。

### Wave 7：删除旧名与最终收口

目标：清理兼容层，形成干净命名。

删除条件：

1. 旧 CLI、旧导入、旧 DatasetKey 已经过至少一个迁移周期。
2. 文档、示例、测试都没有主动使用旧名。
3. release notes 明确列出删除项和替代项。

建议删除项：

| 旧名 | 删除前替代 |
| --- | --- |
| `trader` CLI | 删除，统一 `kairos` |
| `trading` 包 | 删除，统一 `kairos` |
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
  -> Wave 6 kairos 单包名迁移
  -> Wave 7 删除旧名
```

不建议把 Wave 4 和 Wave 6 合并。`adapter` 拆分会改很多业务导入，包名迁移也会改所有导入；两个一起做会让代码审查和回归定位非常痛苦。

## 当前落地状态

截至当前工作区，命名迁移主体已经收口；剩余内容属于后续维护或暂缓验证，不影响当前命名规范结论：

| 批次 | 当前状态 | 已落地点 | 维护项 |
| --- | --- | --- | --- |
| Wave 0 命名冻结 | 已落地 | 增加仓库卫生测试，防止本地运行产物进入提交态；新增 legacy naming shim 检查，防止旧命名模块重新长出真实实现；数据诊断主 API 已从 `DataHealthService` 收敛到 `DataDiagnosticsService`，旧 `DataHealth*` 和 `data health` 已删除；Massive 权限探测主 API 已从 `MassiveReadinessChecker` 收敛到 `MassiveEntitlementDiagnostics`，旧 `MassiveReadiness*` 和 `massive-readiness` 已删除；数据产品完整契约主 API 已从 `DatasetProductSpec` 收敛到 `DataProductContract`；数据/Reference/Pricing/Treasury/Volatility/Research validation 的旧 `models.py` / `transfer_models.py` 兼容入口已删除；新增导入护栏禁止新代码继续依赖旧 `base.py` 和历史兼容入口；新增公开 CLI 护栏禁止旧 `massive-*` 数据命令回流 | 新增模块继续遵守禁用词扫描 |
| Wave 1 Kairos 品牌和 CLI | 已落地（静态验证） | `pyproject.toml` 发布名改为 `kairospy`；只发布 `kairos` CLI；Python import 包名继续是 `kairos`；新增 `kairos init`，支持安装后在任意目录创建项目骨架；包根公开 `initialize_project`，便于脚本化创建用户项目；生成的 `.kairos/project.json` 使用 `root: "."`，避免绑定本机绝对路径；源码仓库即使所在目录仍名为 `trader`，初始化默认项目名也会从 `pyproject.toml` 识别为 `kairos`，防止根元数据回退；README 主路径使用 `pip install kairospy`、`kairos init`、`Kairos/kairos`；旧 `trader` 入口已移除 | Python/wheel 安装验收按用户要求暂缓 |
| Wave 2 fixture/mock | 已落地 | `SyntheticScenario`、`build_synthetic_backtest_dataset`、`backtest synthetic-scenario` 成为唯一主路径；真实实现位于 `kairos/backtest/synthetic_scenarios.py`；合成数据 venue/listing 元数据改为 `synthetic`；旧 `mock.py`、`MockScenario`、`make_mock_dataset`、`backtest mock` 已删除 | 仅保留测试框架自身的 `unittest.mock` 语义 |
| Wave 3 OHLCV/Bar | 已落地 | `OptionDailyOhlcvPipeline`、`SpxwDailyOhlcvPipeline`、`MassiveEquityDailyOhlcvPipeline` 成为真实类名；实现主体位于 `daily_ohlcv.py`、`equity_daily_ohlcv.py`；旧 `day_aggs.py`、`equity_day_aggs.py`、`*DayAgg*` 别名和 `prepare-*-day-aggs` CLI 已删除；CLI 使用 `prepare-spxw-daily-ohlcv`、`prepare-option-daily-ohlcv`、`prepare-equity-daily-ohlcv --provider massive` | 供应商源路径仍保留 `day_aggs_v1` provenance |
| Wave 4 adapter 职责拆分 | 已落地 | 新增 `kairos.ports`、`kairos.ports.venue/execution/market_data/reference_data/account`；协议层真实定义已从 `kairos.adapters.base` 移到 `kairos.ports.venue`，旧 `kairos.adapters.base` 已删除，主导出只保留 `ReferenceDataPort`、`ExecutionPort`、`AccountPort` 等 `*Port`；`kairos.connectors` 已成为实体实现目录，Kairos 品牌层与物理目录均不再保留 `kairos.adapters`；应用层和执行层类型注解开始改用 `*Port`；Binance/IBKR/模拟执行/组合行情主类名已迁移到 `*Gateway` / `*Client`；供应商包 `__all__` 不再导出旧 `*Adapter` 名；Binance/IBKR/transfer package root 不再 re-export 旧 `*Adapter` 兼容别名；IBKR SPXW 期权链采集改为 `IbkrSpxwOptionChainProvider`，真实模块为 `kairos.connectors.ibkr.option_chain_provider`；资金划转边界改为 `TransferGateway`、`BinanceTransferGateway`、`BankTransferGateway`，真实模块改为 `kairos.treasury.transfer_gateway`；Massive 原始响应归档主类改为 `MassiveVendorArchiveClient`，真实模块改为 `kairos.connectors.massive.vendor_archive`；组合行情路由真实模块改为 `kairos.connectors.market_data_router`，新公开入口为 `kairos.connectors.market_data_router`；Binance public market stream 主入口为 `kairos.connectors.binance.market_stream`；Binance REST transport、request signing 和 clock sync 已拆到 `binance.rest_transport` / `binance.request_signing`；Binance reference data discovery 已拆到 `binance.reference_data`；Binance REST snapshot 行情读取已拆到 `binance.market_data_client`；Binance 下单/撤单/订单恢复能力已拆到 `binance.execution_gateway` / `kairos.connectors.binance.execution_gateway`；Binance 账户、余额、仓位读取已拆到 `binance.account_gateway` / `kairos.connectors.binance.account_gateway`；Binance 资金费率结算流水已拆到 `binance.funding_settlement`；Binance user data stream 已拆到 `binance.user_data_stream`；Binance 期权行情快照解析已拆到 `binance.option_market_snapshot`；Binance 订单恢复协调已拆到 `binance.order_recovery`；`kairos.adapters` 目录已删除；测试和示例中的 `adapter` 泛称已收敛到 `gateway`、`connector` 或具体转换格式 | 新增 connector 继续按 Port/Gateway/Client/Provider 命名 |
| Wave 5 research/studies 边界 | 已落地 | 顶层源码研究工作区已从 `research/` 迁到 `studies/`；`kairos init` 也生成用户侧 `studies/` 工作区；公开 examples 研究脚本已从 `examples/research` 迁到 `examples/studies`；已生成的 Study workspace 脚本也从 `research.py` 迁为 `study.py`，导入统一为 `kairos.study_platform.open_study`；包内研究平台统一为 `kairos.study_platform`；旧 `kairos.research` 包已删除；`OptionChainCaptureSpec` 成为期权链采集规格真实类名，`ResearchSpec` 已删除；`OptionCaptureService.capture_snapshot` 成为期权快照采集唯一入口，CLI 并入 `kairos study capture` / `kairos study capture-series` / `kairos study analyze` / `kairos study show`；真实实现位于 `kairos.study_platform.option_capture`；`option_universe_selector.py` 成为期权 universe 选择主模块；期权快照分析主入口改为 `OptionSnapshotAnalysis` / `OptionSnapshotMetricRow` / `analyze_option_snapshot`，真实模块改为 `kairos.study_platform.option_snapshot_analysis`；`ResearchService.capture`、顶层 `kairos research` 命令组、`research.service`、`research.selector`、`research.analyzer` 和分析旧别名已删除；`pyproject.toml` 已精确排除 `kairos.research`、`kairos.research.*`、顶层 `research` / `studies` 工作区；新增仓库卫生测试防止旧研究包、旧类名和旧 CLI 回到安装包 | `validation`、`session`、`workspace` 当前作为上下文明确的领域词保留 |
| Wave 6 kairos 单包名 | 已落地（静态验证） | PyPI 分发名为 `kairospy`，但 `pyproject.toml` 只发布 `kairos` console script；真实实现已位于 `kairos/`；旧 `trader` script、旧 `trading` 包和旧 `kairos.research` 包均已删除；打包配置只包含 `kairos*`，并精确排除旧研究包和源码工作区 | Python/wheel 安装验收按用户要求暂缓 |

这个状态符合“先新名、再迁移调用方、最后删除旧名”的原则。当前阶段已经删除旧项目/包/CLI 命名 `trader` 与 `trading`，并删除 `mock` 回测入口、`day_aggs` 平台入口和主要 `*Adapter` 兼容别名。新文档、新示例和新代码应使用 `kairos`、`synthetic`、`daily_ohlcv`、`Port/Gateway/Client`、`connectors`。

本轮追加进展：

- 高优先级规划文档中的历史 `Adapter` 泛称已收敛到 `Connector`、`Execution Gateway`、`Port Implementation` 或 `Source`，覆盖 examples suite、async dataflow、data usage、market data provider、reference/treasury、research data platform 和 US equity momentum 计划文档。
- 旧 `build/` 与 `kairos.egg-info/` 构建缓存已清理，避免陈旧 `build/lib/research` 误导打包验收；仓库卫生测试新增工作区构建产物护栏。
- 打包边界新增静态护栏：除 `pyproject.toml` 只 include `kairos*` 并 exclude `kairos.research` / `research` / `studies` 外，还禁止新增 `MANIFEST.in`、`setup.py`、`setup.cfg`、setuptools `package-data` 或 `include-package-data` 这类旁路把源码 Study 工作区塞进分发包。
- 顶层测试文件 `test_service.py` 已改为 `test_option_capture.py`，`test_dataset_quality_service.py` 已改为 `test_dataset_quality_assessment.py`，测试列表不再暴露笼统 service 命名。
- 文件级笼统命名已清理：当前工作区不再存在 `service.py`、`models.py`、`base.py`、`utils.py`、`helpers.py`、`*manager*.py`、`*handler*.py` 这类真实源码文件；仓库卫生测试已新增全工作区文件名护栏，覆盖深层生成产物中的 `research.py`、`*adapter*`、`trader` / `trading` 等旧名；剩余 `Service` 类名只在 `LedgerService`、`OptionValuationService`、`DataDiagnosticsService`、`ManagedServiceSpec` 等明确业务边界中使用。
- 仓库卫生测试和 `scripts/check_naming_static.sh` 新增 README 核心命名表唯一性、用户安装路径与源码开发路径分离护栏，以及 `.kairos/project.json` / `kairos.toml` 的 Kairos/Study 项目元数据护栏，防止用户项目初始化体验回退到旧 `trader` / `research` 语义；同时锁住 `kairos` 包根、`connectors`、`ports`、`study_platform` 的公开导出和 `kairos init` 模板，确保 pip 用户只看到 `kairos`、`studies`、`connectors`、`Port/Gateway/Client` 这套命名。
- 仓库卫生测试新增护栏：`kairos.ports` 包根不得重新导出 `Adapter` 旧名，顶层测试不得新增 `test_service.py`、`test_mock.py`、`test_adapter.py` 这类泛称文件。
- 除本审计文档外，产品文档与示例 Markdown 已切到 `kairos` CLI 默认命名；`architecture.md`、system blueprint、async dataflow、research/backtest/live convergence 等标题和命令不再使用 `Trader` / `trader` 作为主名。
- 用户可见文档中的非兼容 `Adapter/adapter`、`Mock/mock data`、`service.py` 残留已清零；仓库卫生测试新增文档默认 CLI 名护栏，防止旧 CLI 命令重新成为产品文档主路径；`mock` 回测兼容入口已删除。
- README、数据指南、examples Python 脚本和 Notebook 示例已统一使用 `from kairos...` / `import kairos...`；Study 工作区入口统一为 `kairos.study_platform`，不重新暴露旧 `kairos.research`。
- 用户可见 examples 研究脚本目录已从 `examples/research` 收敛为 `examples/studies`，并新增仓库卫生测试防止旧目录回流。
- 仓库卫生测试新增用户侧导入护栏，覆盖 README、数据指南、tutorial 和 examples 的 `.py` / `.md` / `.ipynb`，防止公开示例回退到旧 namespace。
- 顶层测试文件 `test_backtest_models.py` 已改为 `test_backtest_fill_contracts.py`，对应测试类改为 `BacktestFillContractTests`，避免用笼统 `models` 掩盖真实验收对象。
- 数据产品契约真实模块已从 `kairos.data.models` 迁到 `kairos.data.contracts`；旧 `kairos.data.models` 已删除，源码、测试和示例的新导入统一走 `data.contracts`。
- 波动率曲面和研究验证契约真实模块已分别从 `kairos.volatility.models`、`kairos.study_platform.validation.models` 迁到 `contracts.py`；旧 `models.py` 已删除，新导入统一走 contracts。
- Reference 静态定义真实模块已从 `kairos.reference.models` 迁到 `kairos.reference.contracts`；旧 `reference.models` 已删除，新导入统一走 `reference.contracts`。
- Treasury 转账真实契约已从 `kairos.treasury.transfer_models` 迁到 `kairos.treasury.transfer_contracts`；旧 `treasury.models` / `transfer_models.py` 已删除，新导入统一走 `transfer_contracts`。
- `kairos.adapters.base`、`kairos.strategies.base`、backtest/pricing/study/treasury 的旧 `service.py` 入口以及 `BacktestService`、`ValuationService`、`ResearchService`、`TreasuryService` 泛名已删除；新增仓库卫生测试禁止新代码继续直接导入旧兼容入口。
- `kairos.connectors` 已成为实体实现目录，并从 `kairos.__init__` 的动态别名中移除 `adapters`；Kairos 品牌层与物理目录均不再保留 `kairos.adapters`，示例入口统一使用 `kairos.connectors`。
- 普通测试层已从 `kairos.adapters.*` 导入迁移到 `kairos.connectors.*`；旧 `kairos.adapters` 包不再保留，仓库卫生测试禁止 tests/examples 回流。
- 测试中的 patch target 也已从 `kairos.adapters.*` 迁到 `kairos.connectors.*`；架构边界测试禁止 domain 直接依赖 connector 实现层。
- `kairos.connectors` 包根已简化为真实连接器 namespace，不再公开 `base`、`composite`、`binance.adapter`、`ibkr.adapter`、旧 `day_aggs` / `equity_day_aggs` / `option_iv` / `readiness` 兼容子模块；`day_aggs` / `equity_day_aggs` / `option_iv` / `readiness` 平台模块已删除，并由 hygiene 护栏防止回流。
- `kairos data provider-entitlement-diagnostics --provider massive` 已成为 Massive 供应商权限和 endpoint 能力诊断唯一命令；旧 `massive-readiness` 已删除。
- `kairos data us-equity-momentum-diagnostics` 已成为 US equity momentum 数据包审计唯一主命令；旧 `us-equity-momentum-readiness` 和 `UsEquityMomentumReadiness` 已删除。
- `kairos backtest spxw-reference-scenario` 已成为 SPXW 固定回放验收主命令；真实模块从 `kairos.backtest.golden` 迁到 `kairos.backtest.spxw_reference_pipeline`，产物 ID 从 `market-slice` 迁到 `market-snapshot`，旧 `golden-spxw` 已删除。
- 依赖源码工作区 `studies.*` 的历史研究命令（如 BTC options readiness、SPXW study readiness、BTC iron condor registration）已从产品 CLI help 隐藏；`studies/` 仍不进入 pip package，安装后公开体验不再暗示这些源码研究模块可用。
- `kairos.__init__` 不再为旧 `task_supervisor`、`runtime_golden`、`runtime_failure_matrix`、`market_slice_storage`、`market_slice_curation` 安装动态子模块别名；Kairos API 测试改为只验证新 `service_supervisor`、`runtime_reference_artifact`、`runtime_failure_policy`、`market_snapshot_*` 入口。
- synthetic scenario 数据规范已从 `HistoricalDataset` / `ContractMetadata` 旧结构名切到 `MarketReplayDataset` / `InstrumentLifecycleSnapshot`。
- synthetic scenario 真实构建代码也已从 `MarketSlice` / `HistoricalDataset` / `ContractMetadata` 兼容别名切到 `MarketSnapshot` / `MarketReplayDataset` / `InstrumentLifecycleSnapshot`；旧 `kairos.backtest.feed` 兼容别名已删除。
- `kairos.application` 包根已停止导出 `live_paper_composition`；旧别名已从 `kairos.application.modes` 删除，新代码使用 `paper_trading_composition`。

## 剩余验收边界

截至当前静态审计，没有发现仍需改名的公开项目名、包名、CLI 名、目录名或打包配置。剩余未证明项不是命名改造本体，而是按用户要求暂缓的运行级验收：

当前非 Python 静态验收入口：

```bash
./scripts/check_naming_static.sh
```

该脚本只依赖 shell、`git`、`rg`、`find` 和 `awk`，用于命名/打包边界静态验收；不会构建 wheel，也不会导入 Python 包。

1. 构建 wheel / sdist 后检查分发文件清单，确认只包含 `kairos*` 包，不包含顶层 `studies/`、Notebook、旧 `research/` 或旧 `trading/`。
2. 在干净临时环境中执行 `pip install kairospy`，确认只安装 `kairos` console script，不安装 `trader`。
3. 在任意空目录执行 `kairos init`，确认生成 `kairos.toml`、`.kairos/project.json`、`studies/starter.py` 和 `config/study.json`，且不生成 `research/` 或 `config/research.json`。
4. 在该外部项目目录运行 starter，确认用户无需停留在源码仓库即可开始自己的 Kairos 量化项目。

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

## 已完成的优先处理项

本轮命名改造已经完成以下优先项：

1. 将产品、README 标题、CLI 目标名定为 `Kairos` / `kairos`，删除旧 `trader` 入口。
2. 清理并确认不再出现提交态 `__pycache__`、`.pyc`、`pyenv/`。
3. `kairos/backtest/mock.py` 已删除，真实实现位于 `synthetic_scenarios.py`。
4. 把 `adapter` 命名按职责拆成 `ExecutionGateway`、`MarketDataClient`、`ReferenceDataClient`、`EventNormalizer`、`Port`。
5. CLI 的 `paper-sma`、`shadow-sma`、`simulate-sma`、`backtest-sma` 已收敛到 `paper`、`shadow`、`simulate`、`backtest` 通用入口。
6. `ResearchSpec` 已改为 `OptionChainCaptureSpec`，避免通用研究规格被 SPXW 默认值占用。
