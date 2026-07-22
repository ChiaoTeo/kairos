# KairoSpy 文件级定位 Inventory

状态：Draft，source-derived + migrated-state inventory

本文件用于支撑 `quant_system_refactor_plan.md`。它不是按当前文件名直接下结论，而是基于每个 Python 文件的 AST 信号生成第一版定位：top-level class/function、docstring、内部 import、以及是否耦合 backtest/connectors/risk/storage 等系统能力。

阅读方式：

- **实际信号**：来自源码的类、函数、docstring。
- **产品视角**：这个文件最终服务哪个内部产品或用户能力。
- **系统视角**：它在系统里承担模型、服务、状态机、gateway、store、artifact 等哪类职责。
- **用户视角**：普通用户、策略作者、研究员、运维是否直接感知。
- **目标归属**：最终产品文件夹。
- **边界备注**：根据源码依赖和符号发现的风险或保留理由。

已迁移或已删除的文件以 `former` 标注，只作为来源说明，不再代表当前代码入口。

## 当前文件来源信号数量

- External Integrations: 76
- Data Product: 37
- Run Runtime: 42
- Research/Validation: 28
- 分析/模型能力: 18
- Portfolio/Account State: 15
- 当前 `trading/` 聚合来源: 14
- 产品族规则: 15
- Execution State Machine: 12
- Market Plane: 26
- Risk/Budget: 12
- Reference Data: 8
- 用户入口产品: 8
- Governance/Operations: 9
- Infrastructure: 6
- Strategy SDK: 3
- 用户工作区产品: 3

## `(root)/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/__init__.py` | doc: Kairos quantitative data, workspace, strategy protocol, and run toolkit. | 用户入口产品 | public Python API wrapper | 直接用户入口 | `kairospy/__init__.py` + `surface/` | 根包只保留稳定公开 API 的懒导出，不承载 use-case 逻辑；实际 use-case 归 `surface/`。 |
| `kairospy/__main__.py` | funcs: main wrapper | 用户入口产品 | thin CLI module wrapper | 直接用户入口 | `surface/cli/main.py` | 仅保留 `python -m kairospy` 包装；实际 CLI dispatch 已迁到 `surface/cli/main.py`。 |
| `kairospy/surface/cli/main.py` | funcs: main | 用户入口产品 | integration boundary, persistence, risk-coupled, trading model consumer | 直接用户入口 | `surface/cli/` | CLI dispatch 当前过厚；目标进入 surface/cli，只做命令解析和 use-case dispatch；`run start --mode live --supervise-live-services` 只表达显式用户意图，不拥有 managed service lifecycle。 |
| `kairospy/surface/cli/output.py` | funcs: resolve_language, render_product_result, render_error, render_status_table, render_key_value_panel, render_command_success | 用户入口产品 | model/helpers | 直接用户入口 | `surface/cli/output.py` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/surface/cli/progress.py` | classes: TerminalProgressMatrix | 用户入口产品 | model/helpers | 直接用户入口 | `surface/cli/progress.py` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/infrastructure/configuration.py` | classes: ConfigError, ConfigValue, BinanceCredentials, KairosProjectConfig; funcs: load_project_config_or_none, set_config_value, unset_config_value | Infrastructure | configuration model/helpers | 内部实现 | `infrastructure/configuration.py` | 已承接旧 `configuration.py`；只处理项目配置发现、解析和写入，不承载业务规则。 |
| `kairospy/surface/product.py` | classes: InputTableRef, DataAddInputError, DataProductNotFoundError, DataLiveDatasetNotConfiguredError, DataDatasetInputError, Data; funcs: data_download, data_apply, data_start, data_add, data_use, data_product_list, run_start | 用户入口产品 | product use-case facade, runtime launcher caller | 直接用户入口 | `surface/product.py` | 用户产品门面；`run start --mode paper` 和配置化 `run start --mode live` 已调用 RuntimeRunLauncher 并写 governance artifact evidence，live provider/market binding 只委托 `integrations/live_ports.py` 与 `runtime/live_binding.py`；默认只记录 Data Product live feed service created evidence，只有 `--supervise-live-services` 或 `[runtime.live.market_binding] supervise_services = true` 才把 `ManagedServiceSpec` bundle 交给 RuntimeRunLauncher；surface 只做参数解析、确认门禁和 use-case 编排，不拥有 RunKernel、profile、connector、live evidence schema、service supervisor 或 artifact repository 规则。 |
| `kairospy/surface/project.py` | classes: ProjectInitResult; funcs: initialize_project, render_project_init | 用户入口产品 | project initialization surface | 直接用户入口 | `surface/project.py` | 已承接旧 `project.py`；负责用户项目初始化和渲染，不承担 workspace/run/data 业务规则。 |
| `kairospy/surface/providers.py` | funcs: providers_list, provider_doctor, data_product_doctor | 用户入口产品 | model/helpers | 直接用户入口 | `surface/providers.py` | provider 诊断门面；目标进入 surface/providers.py，调用 integrations/data doctor。 |

## `trading/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/trading/__init__.py` | former doc: Venue-independent multi-asset trading model. | former 聚合入口 | model/helpers | 不应直接暴露 | 删除 | 已删除；最终目标不保留 `trading` 聚合包，公开入口由 `surface/` 和具体 owner 提供。 |
| `kairospy/trading/capability.py` | former classes: MarketDataKind, OrderType, TimeInForce, MarginMode, PositionMode, ReferenceCapabilities | former mixed owner enums + connector readiness source | model/helpers | 通过 provider doctor 和 runtime readiness 间接可见 | `market/subscriptions.py + execution/orders.py/policy.py + reference/contracts.py + connector metadata/readiness evidence` | 已删除；MarketDataKind/MarketDataCapabilities 由 market 承接，OrderType/TIF/MarginMode/PositionMode/ExecutionCapabilities 由 execution 承接，ReferenceCapabilities 由 reference 承接；不恢复通用 connector capability model。 |
| `kairospy/trading/corporate_action.py` | former classes: CorporateActionType, SplitEvent, CashDividendEvent, StockDividendEvent, InstrumentExchangeEvent, SymbolChangeEvent | former equity lifecycle source | model/helpers | 通过 reference/portfolio view 间接可见 | `products/equity/corporate_actions.py` | 已删除；公司行为事件和服务由 equity 产品 owner 承接，不能放入通用交易总包。 |
| `kairospy/trading/derivative_event.py` | former classes: DerivativeEventType, DerivativePositionEvent | former derivative lifecycle source | model/helpers | 通过 product lifecycle 和 portfolio view 间接可见 | `products/common/lifecycle/derivatives.py` | 已删除；衍生品 lifecycle event 由 products common lifecycle 承接，不属于 run loop 或 strategy archetype。 |
| `kairospy/trading/event.py` | former classes: UnderlyingPriceUpdated, QuoteUpdated, TradeUpdated, GreeksUpdated, OptionChainDiscovered, BrokerConnected; funcs: envelope | former mixed event source | model/helpers | 通过 view/service 间接可见 | `market/events.py + integrations/events.py + governance/events.py` | 已删除；market payload 由 `market/events.py` 承接，broker lifecycle 由 `integrations/events.py` 承接，data/operator warning 由 `governance/events.py` 承接。 |
| `kairospy/trading/execution.py` | former classes: TradeSide, TradeExecution, FundingPayment, DividendPayment | Execution + Portfolio ledger events | model/helpers | 通过 OrderView/PortfolioView 间接可见 | `execution/events.py + portfolio/ledger_events.py` | 已删除；TradeSide/TradeExecution 由 `execution/events.py` 承接，FundingPayment/DividendPayment 由 `portfolio/ledger_events.py` 承接。 |
| `kairospy/trading/identity.py` | former classes: AssetId, Amount, VenueId, InstrumentId, InstitutionId, AccountType | 稳定身份产品 | model/helpers | 通过 view/service 间接可见 | `identity/` | 已由 `identity/` 承接，目标只允许 AccountRef/identity，不承载余额、权限、锁或凭证。 |
| `kairospy/trading/intent.py` | former classes: LegIntent, OpenStructureIntent, CloseStructureIntent, TargetPositionIntent, TargetExposureIntent, CoveredCallIntent | Strategy SDK | model/helpers | 策略作者 API | `strategy/intents.py + strategy/archetypes.py` | 已删除；通用 intent 由 `strategy/intents.py` 承接，CoveredCall/ProtectivePut/CashAndCarry 由 `strategy/archetypes.py` 承接。 |
| `kairospy/trading/ledger.py` | former classes: LedgerBook, LedgerEntryType, LedgerEntry, LedgerTransaction, Ledger | Portfolio/Account State | model/helpers | 通过 PortfolioView 和 run artifact 间接可见 | `portfolio/ledger.py` | 已删除；ledger 事实源由 `portfolio/ledger.py` 承接，策略不能直接写。 |
| `kairospy/trading/market_data.py` | former classes: OptionChain, Quote, Trade, Bar, OrderBookLevel, OrderBookSnapshot | Market Plane | model/helpers | 通过 MarketView 间接可见 | `market/types.py` | 已删除；quote/trade/bar/order book 由 `market/types.py` 承接，是运行时行情类型，不是 Data Product。 |
| `kairospy/trading/market_state.py` | former classes: InstrumentMarketState, MarketState; funcs: apply_market_event | former Market Plane projection source | projection | 通过 MarketView 间接可见 | `market/state.py + market/projections.py` | 已删除；MarketState 和 apply_market_event 由 `market/state.py` 承接，不能包含 portfolio/risk/execution 状态。 |
| `kairospy/trading/order.py` | former classes: TriggerPriceSource, SelfTradePrevention, ExecutionInstructions, OrderStatus, Order, LegFill | Execution State Machine | model/helpers | 通过 OrderView/IntentView 间接可见 | `execution/orders.py + execution/fills.py` | 已删除；order/fill 由 execution owner 承接，provider SDK、outbox store 和 portfolio mutation 另有 owner。 |
| `kairospy/trading/product.py` | former classes: ProductType, OptionRight, ExerciseStyle, SettlementType, SettlementSession, ContractType; funcs: is_option_spec, option_multiplier | Reference Data + 产品族规则 | model/helpers | 通过 ReferenceView 间接可见 | `reference/contracts.py + products/*/contracts.py` | 已删除；contract summary/spec 由 `reference/contracts.py` 承接，产品族行为归 products。 |
| `kairospy/trading/strategy_contract.py` | former classes: StrategyLifecycle, StrategySpec, EconomicIntent | Strategy SDK | model/helpers | 策略作者 API | `strategy/contracts.py` | 已删除；strategy contract 已由 `strategy/contracts.py` 承接。 |

## `data/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/data/__init__.py` | package/export glue | Data Product | model/helpers | 数据产品使用/发布 | `data/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/acquisition.py` | classes: ProviderConnector, ProviderRegistry, CoveragePlanner | Data Product | planner | 数据产品使用/发布 | `data/acquisition/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/acquisition_primitives.py` | classes: TimeRange, AcquisitionRequest, AcquisitionEstimate, AcquisitionLimits, AcquisitionPlan | Data Product | model/helpers | 数据产品使用/发布 | `data/acquisition/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/artifact_audit.py` | classes: GovernedArtifactAudit; funcs: audit_governed_artifact | Data Product | artifact | 数据产品使用/发布 | `data/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/bootstrap.py` | funcs: register_default_products, register_configured_products, default_provider_registry, configured_product_specs | Data Product | integration boundary | 数据产品使用/发布 | `data/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/builders/__init__.py` | package/export glue | Data Product | model/helpers | 数据产品使用/发布 | `data/builders/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/builders/ohlcv.py` | classes: EquityOhlcvSourceBinding, EquityOhlcvDataProductBuilder; funcs: equity_hourly_ohlcv_rows, equity_daily_ohlcv_rows, equity_ohlcv_row, equity_symbol, merge_equity_ohlcv_rows, write_equity_ohlcv_dataset | Data Product | persistence | 数据产品使用/发布 | `data/builders/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/builders/planning.py` | classes: TaskRangePlan, UniversePlan, DataProductTaskPlan | Data Product | model/helpers | 数据产品使用/发布 | `data/builders/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/builders/product_builders.py` | classes: ProductSourceBinding, DatasetBuildResult, DataProductBuilder, DataProductBuilderRegistry | Data Product | model/helpers | 数据产品使用/发布 | `data/builders/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/builtin.py` | classes: BuiltInDataProduct, BuiltInDataProductRegistry, BuiltInHistoricalDataProtocol, BuiltInLiveDataProtocol; funcs: default_builtin_protocol_registry | Data Product | model/helpers | 数据产品使用/发布 | `data/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/catalog.py` | classes: DataCatalog | Data Product | model/helpers | 数据产品使用/发布 | `data/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/client.py` | classes: DataUnavailableError, DataQuery, DatasetClient | Data Product | external client, persistence, trading model consumer | 数据产品使用/发布 | `data/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/columnar_publishing.py` | classes: IntradayColumnarRelease; funcs: publish_intraday_staging_parquet | Data Product | persistence | 数据产品使用/发布 | `data/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/contracts.py` | classes: DatasetLayer, DatasetStorageKind, DatasetStatus, QualityLevel, AcquirePolicy, DataView; funcs: data_release_ref, stable_artifact_hash | Data Product | policy, artifact, view | 数据产品使用/发布 | `data/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/curated.py` | classes: ConsolidatedTradeInput, ConsolidatedTradePolicy, ConsolidatedTradeBuilder | Data Product | policy, persistence | 数据产品使用/发布 | `data/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/diagnostics.py` | classes: DataDiagnosticIssue, DataDiagnosticsService, DatasetReadinessService | Data Product | service | 数据产品使用/发布 | `data/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/external_process.py` | classes: ExternalProcessProductBinding, ExternalProcessDataProductBuilder; funcs: publish_external_process_file, command_tuple | Data Product | persistence | 数据产品使用/发布 | `data/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/feed.py` | classes: ReplaySpec, ReplayEventFeed, ReplaySnapshotFeed; funcs: replay_spec | Data Product | deterministic release replay, market snapshot consumer | 数据产品使用/发布 | `data/` | 负责把 frozen DatasetRelease 暴露为 event/snapshot replay；snapshot 类型来自 `market/snapshots.py`，不依赖 BacktestProfile。 |
| `kairospy/data/freshness.py` | classes: LiveViewFreshnessPolicy, LiveViewFreshnessGateResult, LiveViewSubscriptionBinding, LiveViewFreshnessMonitor; funcs: live_view_freshness_policy, live_view_channel_diagnostics, live_view_freshness_evidence, update_live_view_manifest_freshness, live_view_manifest_path, load_live_view_manifest | Data Product | policy, view | 数据产品使用/发布 | `data/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/historical_service.py` | classes: HistoricalDataService | Data Product | service | 数据产品使用/发布 | `data/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/http.py` | funcs: download, download_json | Data Product | model/helpers | 数据产品使用/发布 | `data/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/live_capture.py` | funcs: register_live_capture_release | Data Product | persistence | 数据产品使用/发布 | `data/live/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/live_service.py` | classes: LiveDataService | Data Product | service | 数据产品使用/发布 | `data/live/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/manifest.py` | classes: DataManifestError, DataManifestDataset, DataManifest | Data Product | model/helpers | 数据产品使用/发布 | `data/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/market_snapshot_curation.py` | funcs: curate_complete_market_snapshots | Data Product | market snapshot release curation | 数据产品使用/发布 | `data/` | 负责从 frozen event release 构建 complete MarketSnapshot release；通用 dataset/manifest contract 来自 `market/snapshots.py`，不拥有回测 profile。 |
| `kairospy/data/market_snapshot_storage.py` | classes: MarketSnapshotStorageDriver | Data Product | market snapshot release storage driver | 数据产品使用/发布 | `data/` | 负责 MarketSnapshot release 的物理读写、schema/version/hash 校验；类型来自 `market/snapshots.py`，不依赖 BacktestProfile。 |
| `kairospy/data/metadata.py` | classes: DataNeedsTimeError, FieldMetadata, DatasetMetadata, DatasetMetadataInference | Data Product | model/helpers | 数据产品使用/发布 | `data/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/preparation.py` | classes: PreparedDataset, DataPromotionPolicyResult, DataPromotionPolicyProfile, DataPreparationService; funcs: evaluate_data_promotion_policy, data_promotion_policy_profile | Data Product | service, policy | 数据产品使用/发布 | `data/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/products.py` | classes: Datasets; funcs: capabilities_payload | Data Product | model/helpers | 数据产品使用/发布 | `data/` | `capabilities_payload` 是历史命名下的 Data Product listing payload，不是 connector capability model；后续公开文案应使用 data product metadata/data_kind。 |
| `kairospy/data/protocols.py` | classes: HistoricalDataRequest, LiveDataRequest, HistoricalDataProtocol, LiveDataProtocol, DataProtocolRegistry | Data Product | model/helpers | 数据产品使用/发布 | `data/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/provider_extensions.py` | classes: ProviderExtensionContext; funcs: provider_extension_specs, register_provider_extensions | Data Product | context | 数据产品使用/发布 | `data/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/publishing.py` | classes: DatasetPublisher; funcs: content_release_id, content_release_id_from_rows, release_path, merge_release_rows, publish_release, register_market_replay_dataset | Data Product | persistence | 数据产品使用/发布 | `data/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/quality.py` | classes: QualityCheck, QualityAssessment, DatasetQualityService | Data Product | service, persistence | 数据产品使用/发布 | `data/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/release_metadata.py` | funcs: ensure_release_metadata, verify_release_metadata | Data Product | persistence | 数据产品使用/发布 | `data/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/snapshot.py` | classes: DataInputSnapshot; funcs: write_data_snapshot | Data Product | persistence | 数据产品使用/发布 | `data/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/infrastructure/storage/source_cache.py` | classes: SourceCacheEntry, SourceCacheStore | Infrastructure | store | 内部实现 | `infrastructure/storage/source_cache.py` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/surface/data_features.py` | classes: SurfaceFeaturePublisher; funcs: load_surface_features | 用户入口产品 | persistence | 直接用户入口 | `surface/data_features.py` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/trade_curation.py` | funcs: curate_sorted_trade_release | Data Product | persistence | 数据产品使用/发布 | `data/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/data/transfer.py` | classes: DatasetCopyResult; funcs: copy_dataset_release | Data Product | model/helpers | 数据产品使用/发布 | `data/publishing/transfer.py or integrations/connectors/transfer/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |

## `market/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/market/__init__.py` | package/export glue | Market Plane | public owner API | 通过 MarketView 间接可见 | `market/` | 新增目标 owner；只导出行情事实、事件和状态投影类型，不承载 Data Product、portfolio 或 connector payload。 |
| `kairospy/market/capture.py` | classes: CanonicalCaptureManifest, CaptureResourceExceeded, RotatingCanonicalCaptureManifest, CanonicalCaptureWriter, CapturedCanonicalEventSource, RotatingCanonicalCaptureWriter, RotatingCapturedCanonicalEventSource | Market Plane | canonical capture artifact/source | 通过 replay、simulation、soak 和 live evidence 间接可见 | `market/capture.py` | 已承接旧 `market_data/capture.py`；负责 canonical market event capture/replay artifact，不拥有 provider acquisition、策略决策或 portfolio state。 |
| `kairospy/market/events.py` | classes: UnderlyingPriceUpdated, QuoteUpdated, TradeUpdated, GreeksUpdated, OptionChainDiscovered, EventEnvelope; funcs: envelope | Market Plane | event contract | 通过 MarketView 和 event source 间接可见 | `market/events.py` | 已承接旧 `trading/event.py` 的 market payload；只允许市场事实事件，不承载 broker lifecycle 或治理告警。 |
| `kairospy/market/forward.py` | funcs: zero_rate, cost_of_carry_forward, parity_forward | Market Plane | forward/rate helper | 通过 pricing/valuation 间接可见 | `market/forward.py` | 已承接旧 `market_data/forward.py`；只计算 market input derived value，不持有 pricing model、strategy rule 或 portfolio state。 |
| `kairospy/market/projections.py` | classes: CanonicalBarSeriesProjection, QuoteState, CanonicalQuoteProjection, OrderBookGap, OrderBookState, CanonicalOrderBookProjection | Market Plane | canonical event projection/read model | 通过 MarketView、feature runtime 和 order book sync 间接可见 | `market/projections.py` | 已承接旧 `market_data/projections.py`；只做 canonical market event 到运行时行情状态/read model 的确定性投影，不写 repository、capture artifact、portfolio 或 execution 状态。 |
| `kairospy/market/quality.py` | funcs: validate_option_observation, blocking_issues | Market Plane | option market observation quality rule | 通过 valuation/capture readiness 间接可见 | `market/quality.py` | 已承接旧 `market_data/quality.py`；只评估 market observation 是否可用，不决定策略、不做 promotion、不写治理 artifact。 |
| `kairospy/market/repository.py` | classes: ParquetMarketEventRepository | Market Plane | source event repository/persistence | 通过 Data Product publishing、DatasetClient 和 replay feed 间接可见 | `market/repository.py` | 已承接旧 `market_data/repository.py`；持久化 source event dataset 和 metadata，不做 provider acquisition，不进入 Strategy Context。 |
| `kairospy/market/soak.py` | classes: MarketDataSoakResult, MarketSoakService, MarketDataRestartCampaignResult; funcs: run_binance_market_soak, run_binance_market_restart_campaign | Market Plane | market stream soak/restart evidence | 通过 provider doctor/runtime readiness 间接可见 | `market/soak.py` | 已承接旧 `market_data/soak.py`；验证 market stream/channel/capture 健康度，不拥有 connector transport 或 live execution。 |
| `kairospy/market/source_events.py` | classes: MarketEventType, MarketEventEnvelope | Market Plane | source event envelope contract | 通过 Data Product、repository、quality gate 和 replay feed 间接可见 | `market/source_events.py` | 已承接旧 `market_data/events.py`；表达来源事件的时间、source identity、record type 和 payload envelope，不替代 `market/events.py` 的运行时 MarketEvent，也不承载 connector DTO。 |
| `kairospy/market/source_quality.py` | classes: QualitySeverity, EventQualityIssue, EventQualityReport; funcs: validate_events, require_publishable | Market Plane | source event quality gate | 通过 Data Product publishing 和 market event repository 间接可见 | `market/source_quality.py` | 已承接旧 `market_data/quality_gate.py`，并把 trading calendar 依赖改到 `products/common/calendars.py`；只判断 source event dataset 是否可发布，不做 research promotion 或 governance audit。 |
| `kairospy/market/slices.py` | classes: DataQualityIssue, InstrumentSnapshot, MarketSliceQualityIssue, MarketInstrumentSlice, MarketSlice | Market Plane | read-only market slice contract for analytics/risk/profile adapters | 通过 MarketView、pricing、risk 和 feature runtime 间接可见 | `market/slices.py` | Market owner contract；用于让 analytics/pricing、risk engine、feature runtime 和 research capture 消费 profile-neutral market slice/instrument snapshot，不再直接 import BacktestProfile `MarketSnapshot` 或 research-local snapshot。 |
| `kairospy/market/snapshots.py` | classes: SettlementType, InstrumentLifecycleSnapshot, MarketSnapshot, DatasetManifest, MarketReplayDataset, MarketSnapshotReplayFeed, MarketSnapshotFeed; funcs: build_manifest | Market Plane | historical market snapshot/replay dataset contract | 通过 Data Product replay、research capture、backtest/simulation profile 间接可见 | `market/snapshots.py` | Market owner contract；拥有通用 historical MarketSnapshot release、manifest、instrument lifecycle snapshot 和 deterministic replay feed contract。MarketSnapshot 持有 data_binding、event_window、available_time、freshness_seconds；replay feed 会把 frozen dataset manifest id 注入未声明绑定的 snapshot。Data Product 负责发布/存储，BacktestProfile 只消费，不得重新拥有 dataset 类型。 |
| `kairospy/market/state.py` | classes: InstrumentMarketState, MarketState; funcs: apply_market_event | Market Plane | projection/state | 通过 MarketView 间接可见 | `market/state.py` | 已承接旧 `trading/market_state.py`；只从 market event 投影行情状态，不写 portfolio/risk/execution 状态。 |
| `kairospy/market/stream.py` | classes: EventSource, OverflowPolicy, StreamClosed, StreamOverflow, ConsumerGap, ChannelMetrics, IterableEventSource, BoundedEventChannel, ConflatedLatestChannel | Market Plane | stream/channel contract | 通过 MarketView、market replay 和 live binding 间接可见 | `market/stream.py` | 已承接旧 `market_data/stream.py`；定义运行时事件源和 backpressure/channel 语义，不承载 Data Product acquisition、capture persistence 或 connector DTO。 |
| `kairospy/market/subscriptions.py` | classes: MarketDataKind, MarketDataCapabilities, DeliveryMode, CapturePolicy, MarketDataRequirement, SubscriptionKey, PlannedSubscription, SubscriptionPlan, SubscriptionCommand, SubscriptionPlanner, SubscriptionReconciler | Market Plane | subscription requirement/support/planner contract | 通过 MarketView 和 provider readiness 间接可见 | `market/subscriptions.py` | 已承接旧 `trading/capability.py` 的 market data kind/support 和旧 `market_data/subscriptions.py` 的 subscription planner；不承载 execution order policy 或 reference capability。 |
| `kairospy/market/types.py` | classes: OptionChain, Quote, Trade, Bar, OrderBookLevel, OrderBookSnapshot, OrderBookDelta, DerivativeMarketState, IndexPrice, MarkPrice, FundingRate, OpenInterest, Greeks, VolatilitySurfacePoint, DayCount, ForwardMethod, RateNode, RateCurve, DividendInput, ForwardEstimate, OptionMarketObservation, MarketQualityIssue, TradingState, TradingStatus | Market Plane | market fact/input model | 通过 MarketView、pricing/valuation 和 capture quality 间接可见 | `market/types.py` | 已承接旧 `trading/market_data.py` 和旧 `market_data/types.py`；quote/trade/bar/order book 是运行时行情类型，RateCurve/ForwardEstimate/OptionMarketObservation 是 market input fact，不是 Data Product repository。 |

## `integrations/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/integrations/__init__.py` | package/export glue | External Integrations | public owner API | 通过 provider doctor/runtime readiness 间接可见 | `integrations/` | 新增目标 owner；导出集成生命周期事实，不暴露 provider DTO。 |
| `kairospy/integrations/events.py` | classes: BrokerConnected, BrokerDisconnected | External Integrations | lifecycle event contract | 通过 runtime/readiness 间接可见 | `integrations/events.py` | 已承接旧 `trading/event.py` 的 broker lifecycle payload；不能进入 MarketEvent，也不能表达策略/组合状态。 |

## `governance/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/governance/__init__.py` | package/export glue | Governance/Operations | public owner API | 通过 run artifact/provider doctor 间接可见 | `governance/` | 新增目标 owner；导出治理事实，不承载订单状态机或策略经济决策。 |
| `kairospy/governance/artifact.py` | classes: RunArtifact, RunArtifactRepository, GovernanceRunArtifactWriter | Governance/Operations | run artifact repository/evidence, runtime artifact writer adapter | 审计/运维可见 | `governance/artifact.py` | 已承接旧 `application/run_artifact.py`；负责 run artifact 持久化、schema hash 和证据归档，并通过 GovernanceRunArtifactWriter 绑定 RunKernel artifact-writer 边界；已写入并校验 Context `context_view_hashes`、`context_hash` 和 `context-view:<view>:<hash>` cross-view evidence refs；BacktestProfile、SimulationProfile、LiveProfile 已通过同一 artifact explain contract 还原 Context evidence；不承载 runtime loop、strategy decision 或 portfolio mutation。 |
| `kairospy/governance/attribution.py` | classes: SignalAttribution, PortfolioAttribution, ExecutionAttribution, RunAttribution; funcs: build_run_attribution | Governance/Operations | attribution evidence builder | 研究/审计/运维可见 | `governance/attribution.py` | 已承接旧 `application/attribution.py`；负责把 run result 转成归因证据，不进入 Strategy Context，也不改写风险或执行状态。 |
| `kairospy/governance/audit.py` | classes: GovernanceAudit; funcs: audit_governance | Governance/Operations | governance audit report | 审计/运维可见 | `governance/audit.py` | 已承接旧 `validation/audit.py`；负责数据、研究验证 artifact、strategy promotion artifact 的一致性审计，不属于 research validation API。 |
| `kairospy/governance/events.py` | classes: DataWarningRaised | Governance/Operations | warning/audit event contract | 通过 readiness/audit/run artifact 间接可见 | `governance/events.py` | 已承接旧 `trading/event.py` 的 data warning payload；用于治理证据，不作为 MarketEvent 投影行情。 |
| `kairospy/governance/incidents.py` | consts: RUNTIME_FAILURE_POLICY_ID; funcs: run_runtime_failure_policy | Governance/Operations | deterministic incident/failure policy artifact | 运维/审计可见 | `governance/incidents.py` | 已承接旧 `application/runtime_failure_policy.py`；运行故障策略演练属于治理证据，不是 RuntimeKernel 主循环，也不是 connector capability 模型。 |
| `kairospy/governance/kill_switch.py` | classes: KillSwitchResult, KillSwitch | Governance/Operations | emergency control, integration boundary | 运维直接可见 | `governance/kill_switch.py` | 已承接旧 `orchestration/kill_switch.py`；负责 fail-closed/reduce-only 运维控制，不承载 order state machine 或 provider SDK。 |
| `kairospy/governance/observability.py` | classes: AlertSeverity, OperationalAlert, OperationalMonitor | Governance/Operations | operational alert/readiness signal | 运维直接可见 | `governance/observability.py` | 已承接旧 `orchestration/monitoring.py`；负责运行可观测告警，不拥有 supervisor task 生命周期。 |
| `kairospy/governance/promotion.py` | classes: PromotionEvidence, PromotionDecision, PromotionPolicy, PromotionError | Governance/Operations | promotion gate evidence/policy | 审计/运维可见 | `governance/promotion.py` | 承接 strategy lifecycle promotion 的治理证据；必须引用 dataset/strategy/config hash 和 gate/readiness evidence，不修改策略经济决策。 |
| `kairospy/governance/readiness.py` | classes: ReadinessStatus, ReadinessEvidence, ReadinessDecision, ReadinessError; funcs: decide_readiness, require_readiness | Governance/Operations | run readiness gate evidence | 运维/启动 gate 可见 | `governance/readiness.py` | 承接 profile 启动前 readiness evidence；只判断是否 fail-fast/degraded，不定义 connector capability model，不进入 Strategy Context。 |
| `kairospy/governance/reconciliation.py` | classes: ReconciliationDifference, ReconciliationReport, ReconciliationService | Governance/Operations | reconciliation service/report | 运维直接可见 | `governance/reconciliation.py` | 已承接旧 `orchestration/reconciliation.py`；负责本地账本/仓位与外部账户状态差异证据，不持有 runtime store 物理实现。 |
| `kairospy/governance/strategy_monitoring.py` | classes: StrategyHealth, StrategyMonitoringLimits, StrategyMonitoringSnapshot, StrategyHealthDecision, StrategyHealthMonitor | Governance/Operations | strategy health decision | 策略运行结果和运维可见 | `governance/strategy_monitoring.py` | 已承接旧 `orchestration/strategy_monitoring.py`；负责策略健康度和资本降级决策，不生成策略 intent。 |

## `market_data/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/market_data/__init__.py` | former package/export glue | former Market Plane package API | package/export glue | 内部实现 | 删除 | 已删除；最终目标不保留 `market_data` 包，所有 Market Plane API 由 `market/` 及其子模块提供。 |
| `kairospy/market_data/capture.py` | former classes: CanonicalCaptureManifest, CaptureResourceExceeded, RotatingCanonicalCaptureManifest, CanonicalCaptureWriter, CapturedCanonicalEventSource, RotatingCanonicalCaptureWriter | former Market Plane capture source | canonical capture artifact/source | 内部实现 | `market/capture.py` | 已删除；canonical capture writer/source 由 `market/capture.py` 承接。 |
| `kairospy/market_data/events.py` | former classes: MarketEventType, MarketEventEnvelope | former Market Plane source event source | source event envelope contract | 内部实现 | `market/source_events.py` | 已删除；source event envelope 由 `market/source_events.py` 承接，旧 `market_data/` 不再导出 source event API。 |
| `kairospy/market_data/forward.py` | former funcs: zero_rate, cost_of_carry_forward, parity_forward | former Market Plane forward helper source | forward/rate helper | 内部实现 | `market/forward.py` | 已删除；forward/rate helper 由 `market/forward.py` 承接，旧 `market_data/` 不再导出 forward API。 |
| `kairospy/market_data/projections.py` | former classes: CanonicalBarSeriesProjection, QuoteState, CanonicalQuoteProjection, OrderBookGap, OrderBookState, CanonicalOrderBookProjection | former Market Plane projection source | canonical event projection/read model | 内部实现 | `market/projections.py` | 已删除；canonical event 到 Market Plane read model 的投影由 `market/projections.py` 承接，旧 `market_data/` 不再导出 projection API。 |
| `kairospy/market_data/quality.py` | former funcs: validate_option_observation, blocking_issues | former Market Plane quality helper source | option market observation quality rule | 内部实现 | `market/quality.py` | 已删除；option market observation quality rule 由 `market/quality.py` 承接，旧 `market_data/` 不再导出 observation quality API。 |
| `kairospy/market_data/quality_gate.py` | former classes: QualitySeverity, EventQualityIssue, EventQualityReport; funcs: validate_events, require_publishable | former Market Plane source event quality gate | source event quality gate | 内部实现 | `market/source_quality.py` | 已删除；source event quality gate 由 `market/source_quality.py` 承接，旧 `market_data/` 不再导出 validate_events/require_publishable，且不再依赖 backtest calendar。 |
| `kairospy/market_data/repository.py` | former classes: ParquetMarketEventRepository | former Market Plane repository source | source event repository/persistence | 内部实现 | `market/repository.py` | 已删除；source event repository 由 `market/repository.py` 承接，旧 `market_data/` 不再导出 repository API。 |
| `kairospy/market_data/soak.py` | former classes: MarketDataSoakResult, MarketSoakService, MarketDataRestartCampaignResult; funcs: run_binance_market_soak, run_binance_market_restart_campaign | former Market Plane soak source | market stream soak/restart evidence | 内部实现 | `market/soak.py` | 已删除；market stream soak/restart evidence 由 `market/soak.py` 承接，旧 `market_data/` 不再导出 soak API。 |
| `kairospy/market_data/stream.py` | former classes: EventSource, OverflowPolicy, StreamClosed, StreamOverflow, ConsumerGap, ChannelMetrics, IterableEventSource, BoundedEventChannel, ConflatedLatestChannel | former Market Plane stream source | stream/channel contract | 内部实现 | `market/stream.py` | 已删除；运行时事件源和 channel/backpressure 契约由 `market/stream.py` 承接，旧 `market_data/` 不再导出 stream API。 |
| `kairospy/market_data/subscriptions.py` | former classes: DeliveryMode, CapturePolicy, MarketDataRequirement, SubscriptionKey, PlannedSubscription, SubscriptionPlan | former Market Plane subscription source | policy, planner, trading model consumer | 内部实现 | `market/subscriptions.py` | 已删除；subscription requirement/support/planner 由 `market/subscriptions.py` 承接，旧 `market_data/` 不再导出 subscription API。 |
| `kairospy/market_data/types.py` | former classes: DayCount, ForwardMethod, RateNode, RateCurve, DividendInput, ForwardEstimate, OptionMarketObservation, MarketQualityIssue | former Market Plane market input source | market fact/input model | 内部实现 | `market/types.py` | 已删除；rate/forward/option observation/quality issue 等 market input fact 由 `market/types.py` 承接，旧 `market_data/` 不再导出 market input model。 |

## `reference/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/reference/__init__.py` | doc: Point-in-time reference data model for assets, products and tradable contracts. | Reference Data | model/helpers | 通过 view/service 间接可见 | `reference/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/reference/access.py` | funcs: definition_at, contract_spec, product_type, trade_cash_asset, settlement_asset | Reference Data | trading model consumer | 通过 view/service 间接可见 | `reference/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/reference/catalog.py` | classes: VersionedRepository, ReferenceCatalog | Reference Data | repository, trading model consumer | 通过 view/service 间接可见 | `reference/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/reference/contracts.py` | classes: ProductType, ReferenceCapabilities, AssetType, EntityType, BenchmarkType, AssetDefinition, EntityDefinition, VenueType | Reference Data | contract/support model, trading model consumer | 通过 view/service 间接可见 | `reference/` | 已承接旧 `trading/capability.py` 的 ReferenceCapabilities；reference support 只表达 product support，不承载 market/execution readiness。 |
| `kairospy/reference/factory.py` | funcs: publish_instrument, product_id_for, add_instrument_references | Reference Data | trading model consumer | 通过 view/service 间接可见 | `reference/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/reference/identity.py` | classes: EntityId, BenchmarkId, ProductId, SeriesId, ListingId, ProviderId | Reference Data | trading model consumer | 通过 view/service 间接可见 | `reference/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/reference/repository.py` | classes: ReferenceCatalogRepository; funcs: instrument_to_primitive, instrument_from_primitive | Reference Data | repository, persistence, trading model consumer | 通过 view/service 间接可见 | `reference/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/reference/sync.py` | classes: ReferenceSyncResult, ReferenceSyncService | Reference Data | service | 通过 view/service 间接可见 | `reference/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |

## `products/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/products/__init__.py` | package/export glue | 产品族规则 | model/helpers | 通过 view/service 间接可见 | `products/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/products/calculators.py` | classes: PositionCalculator, SpotCalculator, OptionCalculator, LinearContractCalculator, InverseContractCalculator, QuantoContractCalculator | 产品族规则 | trading model consumer | 通过 view/service 间接可见 | `products/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/products/common/__init__.py` | package/export glue | 产品族规则 | model/helpers | 通过 view/service 间接可见 | `products/common/` | 新增产品公共生命周期 owner；不作为一级产品目录。 |
| `kairospy/products/common/calendars.py` | classes: TradingSession, TradingCalendar, AlwaysOpenCalendar, CalendarRegistry; funcs: us_market_holidays, us_market_early_closes | 产品族规则 | calendar/session contract | 通过 reference/product lifecycle、market quality 和 data builders 间接可见 | `products/common/calendars.py` | 已承接从 backtest 依赖中抽出的公共交易日历能力；connector/features/market quality 已改为依赖这里，不再依赖 `backtest.calendar`。 |
| `kairospy/products/common/lifecycle/__init__.py` | package/export glue | 产品族规则 | public owner API | 通过 view/service 间接可见 | `products/common/lifecycle/` | 导出通用 lifecycle event，不承载 backtest/simulation/live 主循环。 |
| `kairospy/products/common/lifecycle/derivatives.py` | classes: DerivativeEventType, DerivativePositionEvent | 产品族规则 | lifecycle event model | 通过 product lifecycle 和 portfolio view 间接可见 | `products/common/lifecycle/derivatives.py` | 已承接旧 `trading/derivative_event.py`；只定义衍生品 position lifecycle fact，不承载 connector DTO 或 backtest loop。 |
| `kairospy/products/common/lifecycle/settlement.py` | classes: AssetFlow, PositionFlow, SettlementResolution, SettlementResolver | 产品族规则 | settlement lifecycle resolver | 通过 product lifecycle 和 portfolio view 间接可见 | `products/common/lifecycle/settlement.py` | 已承接旧 `lifecycle/settlement.py`；只处理产品交割规则解析，不承载 run profile、connector DTO 或 strategy decision。 |
| `kairospy/products/crypto_option/__init__.py` | package/export glue | 产品族规则 | model/helpers | 通过 view/service 间接可见 | `products/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/products/crypto_option/settlement.py` | classes: CryptoOptionSettlementEvent, CryptoOptionSettlementService, DurableCryptoOptionSettlementService | 产品族规则 | service, trading model consumer | 通过 view/service 间接可见 | `products/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/products/equity/__init__.py` | package/export glue | 产品族规则 | model/helpers | 通过 view/service 间接可见 | `products/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/products/equity/corporate_actions.py` | classes: CorporateActionType, SplitEvent, CashDividendEvent, StockDividendEvent, InstrumentExchangeEvent, SymbolChangeEvent, DelistingEvent, CorporateActionService | 产品族规则 | lifecycle event model/service, trading model consumer | 通过 view/service 间接可见 | `products/equity/corporate_actions.py` | 已承接旧 `trading/corporate_action.py`；定义 equity lifecycle fact 并应用到 portfolio ledger/reference，不承载策略信号或 execution command。 |
| `kairospy/products/future/__init__.py` | package/export glue | 产品族规则 | model/helpers | 通过 view/service 间接可见 | `products/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/products/future/settlement.py` | classes: DerivativeLifecycleService | 产品族规则 | service, trading model consumer | 通过 view/service 间接可见 | `products/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/products/listed_option/__init__.py` | package/export glue | 产品族规则 | model/helpers | 通过 view/service 间接可见 | `products/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/products/listed_option/lifecycle.py` | classes: PhysicalOptionEventType, PhysicalOptionEvent, OptionLifecycleService | 产品族规则 | service, trading model consumer | 通过 view/service 间接可见 | `products/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/products/perpetual/__init__.py` | package/export glue | 产品族规则 | model/helpers | 通过 view/service 间接可见 | `products/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/products/perpetual/funding.py` | classes: FundingEngine | 产品族规则 | engine, trading model consumer | 通过 view/service 间接可见 | `products/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |

## `analytics/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/analytics/__init__.py` | package/export glue | 分析/模型能力 | owner namespace | 通过 view/service 间接可见 | `analytics/` | 新增目标 owner；只作为 features/pricing/volatility 的二级能力命名空间，不直接暴露策略 Context 或运行模式。 |

## `analytics/pricing/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/analytics/pricing/__init__.py` | package/export glue | 分析/模型能力 | model/helpers | 通过 view/service 间接可见 | `analytics/pricing/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/analytics/pricing/black.py` | funcs: black_scholes, black76, price_with_volatility | 分析/模型能力 | trading model consumer | 通过 view/service 间接可见 | `analytics/pricing/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/analytics/pricing/context.py` | classes: PricingContext, PricingContextResolver | 分析/模型能力 | context, trading model consumer | 通过 view/service 间接可见 | `analytics/pricing/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/analytics/pricing/implied_vol.py` | funcs: price_bounds, implied_volatility | 分析/模型能力 | trading model consumer | 通过 view/service 间接可见 | `analytics/pricing/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/analytics/pricing/option_pricing_contracts.py` | classes: PricingModel, SolverStatus, PricingInput, PricingResult, ImpliedVolResult | 分析/模型能力 | trading model consumer | 通过 view/service 间接可见 | `analytics/pricing/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/analytics/pricing/option_valuation.py` | classes: InstrumentValuation, ValuationSnapshot, OptionValuationService | 分析/模型能力 | valuation service, market-slice consumer | 通过 FeatureView/valuation evidence 间接可见 | `analytics/pricing/` | 已切断对 BacktestProfile `MarketSnapshot` 和 research `InstrumentSnapshot` 的直接 import；输入是 `market/slices.py` 的 `MarketSlice` contract，输出仍保持传入 market slice 的具体 dataclass 类型并附加 ValuationSnapshot。ValuationSnapshot 继承输入 market slice 的 available_time，Strategy `FeatureView` 只消费 valuation evidence，不直接暴露 service。 |

## `analytics/volatility/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/analytics/volatility/__init__.py` | package/export glue | 分析/模型能力 | model/helpers | 通过 view/service 间接可见 | `analytics/volatility/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/analytics/volatility/calibration.py` | funcs: calibrate_svi | 分析/模型能力 | model/helpers | 通过 view/service 间接可见 | `analytics/volatility/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/analytics/volatility/contracts.py` | classes: CalibrationStatus, VolObservation, SviParameters, SmileCalibration, ArbitrageDiagnostics, SurfaceSnapshot | 分析/模型能力 | surface evidence contract | 通过 view/service 间接可见 | `analytics/volatility/` | SurfaceSnapshot 持有 available_time，表达 calibration output 对策略何时合法可见；不承载策略模板或运行模式语义。 |
| `kairospy/analytics/volatility/surface.py` | funcs: build_surface, surface_implied_volatility, diagnose_surface | 分析/模型能力 | surface builder/query helper | 通过 view/service 间接可见 | `analytics/volatility/` | build_surface 接收并校验 input available_time，surface input_hash 包含 available_time，确保同一 as_of 下不同可见时间不会被混成同一输入证据。 |
| `kairospy/analytics/volatility/svi.py` | funcs: total_variance, implied_volatility | 分析/模型能力 | model/helpers | 通过 view/service 间接可见 | `analytics/volatility/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |

## `analytics/features/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/analytics/features/__init__.py` | package/export glue | 分析/模型能力 | model/helpers | 通过 view/service 间接可见 | `analytics/features/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/analytics/features/option_skew.py` | classes: OptionSkewFactorConfig, OptionSkewFactorRuntime, OptionFearCoolingFactorRuntime | 分析/模型能力 | feature runtime, market-slice + valuation evidence consumer | 通过 FeatureView 间接可见 | `analytics/features/` | 已切断对 BacktestProfile `MarketSnapshot` 的直接 import；通过 `MarketSlice` + `ValuationSnapshot` 更新 factor state，并把 market available_time 写入 FactorSnapshot。仍应保持为 analytics feature，不进入 Strategy Context 或 runtime profile。 |
| `kairospy/analytics/features/runtime.py` | classes: FactorQuality, FactorSpec, FactorSnapshot, FactorRuntime, FactorRegistry, CanonicalBarFactorRuntime; funcs: implementation_hash, snapshots_hash | 分析/模型能力 | factor runtime contract | 通过 view/service 间接可见 | `analytics/features/` | FactorSnapshot 持有 input_identity、state_hash、available_time，是 FeatureView 的 owner-side 输入证据；不暴露 feature recompute service 或模型内部状态。 |
| `kairospy/analytics/features/sma.py` | classes: SmaFactorConfig, SmaFactorRuntime; funcs: batch_sma_factors | 分析/模型能力 | canonical bar factor runtime | 通过 view/service 间接可见 | `analytics/features/` | SmaFactorRuntime 从 canonical event 继承 available_time；batch helper 没有 event envelope 时以 bar.end 作为可见时间。 |
| `kairospy/analytics/features/us_equity_momentum.py` | classes: UsEquityMomentumPolicy, UsEquityMomentumDatasetBuilder | 分析/模型能力 | policy, persistence, backtest-coupled | 通过 view/service 间接可见 | `analytics/features/` | 依赖 backtest；目标需要切断运行模式泄漏。 |
| `kairospy/analytics/features/us_equity_momentum_diagnostics.py` | classes: UsEquityReadinessCheck, UsEquityMomentumDiagnostics | 分析/模型能力 | model/helpers | 通过 view/service 间接可见 | `analytics/features/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/analytics/features/volatility.py` | classes: BtcIvRvFeatureBuilder, BtcTermSkewFeatureBuilder, BtcDeribitTradeSkewFeatureBuilder; funcs: build_iv_rv_panel, build_deribit_trade_skew_panel, build_term_skew_panel | 分析/模型能力 | persistence | 通过 view/service 间接可见 | `analytics/features/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |

## `strategy/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/strategy/__init__.py` | package/export glue, exports Context/View/schema API | Strategy SDK | public API surface | 策略作者 API | `strategy/` | 已导出 Context、七个 View、ViewSchema/ViewFieldSchema 和 schema/hash helper；后续应避免导出运行模式和 connector。 |
| `kairospy/strategy/archetypes.py` | classes: CoveredCallIntent, ProtectivePutIntent, CashAndCarryIntent | Strategy SDK | archetype model | 策略作者 API | `strategy/` | 已把具体策略模板意图从旧 trading 拆出；后续可进一步改为 builder 输出通用 intent。 |
| `kairospy/strategy/contracts.py` | classes: StrategyLifecycle, StrategySpec, EconomicIntent | Strategy SDK | contract/evidence | 策略作者 API | `strategy/` | 已把 Strategy SDK contract 从旧 trading 拆出；产品类型依赖 `reference.contracts.ProductType`，不再依赖旧 `trading.product`。 |
| `kairospy/strategy/intents.py` | classes: LegIntent, OpenStructureIntent, CloseStructureIntent, TargetPositionIntent, TargetExposureIntent, HedgeIntent, TransferIntent, CancelIntent | Strategy SDK | strategy output language | 策略作者 API | `strategy/` | 已成为通用策略输出语言；仍依赖 execution primitive `TradeSide`/`TimeInForce`，后续随 execution owner 拆分继续收口。 |
| `kairospy/strategy/protocols.py` | classes: StrategyDecision, Context, Strategy | Strategy SDK | context, decision audit, strategy intent consumer | 策略作者 API | `strategy/` | Context 已收窄为七个 View，并暴露 view_schemas、view_hashes、context_hash；公开策略协议只保留 `Strategy`，策略读取 Context 并返回 `strategy.intents.Intent`，`StrategyDecision` 仅作为审计/解释记录。 |
| `kairospy/strategy/views.py` | classes: ViewFieldSchema, ViewSchema, MarketView, PortfolioView, FeatureView, ReferenceView, OrderView, IntentView, BudgetView; funcs: context_view_schemas, view_schema, view_hash | Strategy SDK | stable read model, view schema contract | 策略作者 API | `strategy/` | 已成为策略唯一可读输入 schema；七个 View 均暴露字段级 schema、time semantics、forbidden dependency、schema hash 和实例 view hash，不承载 service 或 mutable state。MarketView 已暴露 data_binding、event_window、available_time、freshness_seconds；FeatureView 已暴露 feature input available_time；PortfolioView 已暴露 reporting_asset、accounts、balances、valuation_status、ledger/account/market evidence、ledger_hash/state_hash；ReferenceView 已暴露 active instrument/product/listing/route identity、contract summary、reference version window、integrity evidence 和 catalog_hash；BudgetView 已暴露 risk/allocation decision counts、risk/limit/governance hashes、reduce-only/blocked reason 和 state_hash；OrderView/IntentView 已从 durable order、outbox command、execution record 投影 client/venue order id、command status、intent progress、execution count、last state time 和 state_hash，用于解释策略当时合法可见的行情、组合、特征、reference、预算和执行状态来源。 |
| `kairospy/strategy/runtime.py` | classes: GovernedStrategyRuntime | Strategy SDK | runtime, trading model consumer | 策略作者 API | `strategy/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |

## `portfolio/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/portfolio/__init__.py` | package/export glue | Portfolio/Account State | owner public surface | 策略运行结果和运维可见 | `portfolio/` | 根 owner 只导出 ledger fact、ledger events 和 account port contract；不导出 mutable projection service 给策略 Context。 |
| `kairospy/portfolio/account_ports.py` | classes: VenueBalance, AccountState, AccountPort | Portfolio/Account State | account state port contract | 通过 PortfolioView 和 readiness/reconciliation 间接可见 | `portfolio/` | 定义外部账户状态的稳定 port/read model；provider adapter 仍在 `integrations/connectors/*`，credential 和 account lock 不进入 PortfolioView。 |
| `kairospy/portfolio/ledger.py` | classes: LedgerBook, LedgerEntryType, LedgerEntry, LedgerTransaction, Ledger | Portfolio/Account State | ledger fact source | 通过 PortfolioView 和 run artifact 间接可见 | `portfolio/` | 唯一 ledger fact 源；策略不能直接写，runtime/execution/product lifecycle 只能通过 owner service 产生 ledger transaction。 |
| `kairospy/portfolio/ledger_events.py` | classes: FundingPayment, DividendPayment | Portfolio/Account State | product/venue ledger event facts | 通过 PortfolioView 和 run artifact 间接可见 | `portfolio/` | 承接 funding/dividend 这类 portfolio ledger event；不放在 execution event，也不进入 strategy intent。 |
| `kairospy/portfolio/projection.py` | funcs: portfolio_view_from_snapshot | Portfolio/Account State | strategy view projection bridge | 策略作者通过 Context 间接可见 | `portfolio/` | portfolio owner 的薄投影入口；复用 `portfolio/accounting/portfolio.py` 的 PortfolioSnapshot，并结合 ledger、account state、MarketView evidence 生成 Strategy `PortfolioView`，不重复 accounting 规则，不暴露 ledger writer 或 account gateway。 |

## `portfolio/accounting/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/portfolio/accounting/__init__.py` | package/export glue | Portfolio/Account State | model/helpers | 策略运行结果和运维可见 | `portfolio/accounting/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/portfolio/accounting/conversion.py` | classes: ConversionRate, ConversionResult, AssetConversionGraph | Portfolio/Account State | trading model consumer | 策略运行结果和运维可见 | `portfolio/accounting/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/portfolio/accounting/ledger.py` | classes: LedgerService | Portfolio/Account State | service, trading model consumer | 策略运行结果和运维可见 | `portfolio/accounting/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/portfolio/accounting/portfolio.py` | classes: AssetBalance, Position, PortfolioSnapshot, Portfolio | Portfolio/Account State | trading model consumer | 策略运行结果和运维可见 | `portfolio/accounting/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |

## `risk/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/risk/__init__.py` | doc: Pre- and post-trade risk controls. | Risk/Budget | model/helpers | 策略运行结果和运维可见 | `risk/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/risk/analytics.py` | classes: PnLExplain, TailRiskResult; funcs: explain_scenario, historical_var_es | Risk/Budget | model/helpers | 策略运行结果和运维可见 | `risk/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/risk/covered_call.py` | former funcs: validate_covered_call | former strategy archetype risk validator | trading model consumer | 策略运行结果和运维可见 | 删除 | 已删除；covered call 抵押校验已收口到 `risk/extensions/covered_call.py`，不能作为 `risk/` 根目录核心模块回流。 |
| `kairospy/risk/engine.py` | classes: PortfolioRiskSnapshot, ComboQuote, RiskDecisionType, RiskDecision, RiskEngine | Risk/Budget | pre/post-trade risk decision, market-slice + portfolio protocol consumer | 策略运行结果和运维可见 | `risk/` | 已切断对 BacktestProfile `MarketSnapshot`、`PortfolioSnapshot` 和 `combo_quote` 的直接 import；RiskEngine 只依赖 `MarketSlice`、risk-owned `PortfolioRiskSnapshot` protocol、ReferenceCatalog 和 strategy intents。后续可把 PortfolioRiskSnapshot 映射到 Strategy `PortfolioView`/`BudgetView`。 |
| `kairospy/risk/extensions/__init__.py` | package/export glue | Risk/Budget | risk extension namespace | 策略运行结果和运维可见 | `risk/extensions/` | 可选产品/策略模板风控扩展入口；不属于 core `RiskEngine`，不定义全局 capability model。 |
| `kairospy/risk/extensions/covered_call.py` | classes: CoveredCallCollateralRequest, CoveredCallCollateralEvidence; funcs: covered_call_collateral_evidence, validate_covered_call_collateral | Risk/Budget | covered call collateral risk extension/evidence | 策略运行结果和运维可见 | `risk/extensions/` | 只消费 archetype-neutral request、account、ledger、reference catalog，输出 collateral evidence；不 import `strategy.archetypes`，不让 core risk 认识具体策略模板。 |
| `kairospy/risk/limits.py` | classes: RiskLimits | Risk/Budget | model/helpers | 策略运行结果和运维可见 | `risk/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/risk/margin.py` | classes: MarginResult, MarginPolicy, SecuritiesCashPolicy, SecuritiesMarginApproximationPolicy, CryptoSpotPolicy, CryptoDerivativesPolicy | Risk/Budget | policy | 策略运行结果和运维可见 | `risk/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/risk/option_structure.py` | funcs: maximum_expiry_loss | Risk/Budget | trading model consumer | 策略运行结果和运维可见 | `risk/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/risk/portfolio_governance.py` | classes: AllocationDecisionType, StrategyAllocation, AllocationDecision, PortfolioAllocator, PositionSizingDecision, PositionSizer | Risk/Budget | decision, trading model consumer | 策略运行结果和运维可见 | `risk/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/risk/scenarios.py` | classes: RevaluationPosition, Scenario, InstrumentScenarioResult, ScenarioResult, ScenarioEngine; funcs: standard_scenario_grid | Risk/Budget | engine, trading model consumer | 策略运行结果和运维可见 | `risk/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/risk/strategy_positions.py` | classes: StrategyPosition, NettedPosition, StrategyPositionBook | Risk/Budget | trading model consumer | 策略运行结果和运维可见 | `risk/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/risk/view.py` | classes: RiskExposure, UnifiedRiskView; funcs: build_risk_view | Risk/Budget | view, risk-coupled, trading model consumer | 策略运行结果和运维可见 | `risk/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |

## `execution/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/execution/__init__.py` | package/export glue | Execution State Machine | model/helpers | 策略运行结果和运维可见 | `execution/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/execution/calibration.py` | classes: ExecutionCalibrationRelease; funcs: load_execution_calibration_release, build_execution_calibration_release | Execution State Machine | integration boundary, persistence, trading model consumer | 策略运行结果和运维可见 | `execution/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/execution/command.py` | classes: OutboxStatus, OrderCommand, OutboxRecord | Execution State Machine | outbox, integration boundary | 通过 OrderView/IntentView 和 run artifact 间接可见 | `execution/` | 已成为 durable command/outbox evidence 事实源；通过 `strategy/views.py` 投影给策略只读 OrderView/IntentView，不暴露 outbox writer、gateway 或 store。 |
| `kairospy/execution/events.py` | classes: TradeSide, TradeExecution | Execution State Machine | execution/fill event fact | 通过 IntentView、PortfolioView 和 run artifact 间接可见 | `execution/` | 已承接旧 `trading/execution.py` 中的 trade execution 事实；为 IntentView 的 execution_event_count/last_execution_at 和 portfolio ledger ingestion 提供执行回报事实，不承接 funding/dividend ledger event。 |
| `kairospy/execution/fills.py` | classes: LegFill, Fill, Settlement | Execution State Machine | fill/settlement facts | 通过 run artifact、PortfolioView 间接可见 | `execution/` | 已从旧 trading order 模型拆出；后续 settlement 可随 product/portfolio lifecycle 继续细分。 |
| `kairospy/execution/ingestion.py` | classes: ExecutionIngestionService, DurableExecutionIngestionService, DurableAccountingIngestionService | Execution State Machine | service, integration boundary, trading model consumer | 策略运行结果和运维可见 | `execution/` | 依赖 connector；目标需要改为 integrations port contract + readiness evidence。 |
| `kairospy/execution/intent_status.py` | classes: IntentStatus, IntentScope, IntentExecutionView, IntentExecutionTracker; funcs: intent_scope | Execution State Machine | intent progress fact/read model | 通过 IntentView 间接可见 | `execution/` | 已成为 intent progress owner；`IntentView` 只读取其进度事实并合并 durable order/outbox/execution evidence，不暴露 tracker mutator。 |
| `kairospy/execution/order_state.py` | classes: DurableOrderStatus, DurableOrderRecord; funcs: require_order_transition | Execution State Machine | durable order lifecycle fact | 通过 OrderView/IntentView 和 recovery artifact 间接可见 | `execution/` | 已成为 durable order lifecycle evidence 事实源；策略只能通过 Strategy View 看到状态摘要，runtime 只编排 recovery，不拥有状态机。 |
| `kairospy/execution/orders.py` | classes: OrderType, TimeInForce, MarginMode, PositionMode, ExecutionInstructions, ExecutionCapabilities, OrderStatus, OrderLeg, Order | Execution State Machine | order model/support contract | 通过 OrderView/IntentView 间接可见 | `execution/` | 已承接旧 `trading/order.py` 和 `trading/capability.py` 的 execution primitive；为 OrderView/IntentView 提供订单语义，但不承载 provider SDK、outbox store 或 portfolio mutation。 |
| `kairospy/execution/outbox.py` | classes: DurableOrderCommandService, DurableOrderDispatcher | Execution State Machine | durable command service, integration boundary | 通过 run artifact 和 OrderView/IntentView evidence 间接可见 | `execution/` | 已成为 submit-before-gateway 的 durable command service；它使用 runtime/application、kill switch 和 execution router，但不导出到 Strategy Context，且类型依赖按需加载以避免 runtime/governance import cycle。 |
| `kairospy/execution/planner.py` | classes: LeggingPolicy, NativeComboPlan, SequentialLegPlan; funcs: plan_combo | Execution State Machine | policy, integration boundary, trading model consumer | 策略运行结果和运维可见 | `execution/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/execution/policy.py` | classes: ExecutionMode, PartialFillPolicy, ExecutionPolicy | Execution State Machine | policy, trading model consumer | 策略运行结果和运维可见 | `execution/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/execution/recovery.py` | classes: OrderRecoveryReport, VenueOrderRecoveryService | Execution State Machine | service, report, integration boundary, trading model consumer | 策略运行结果和运维可见 | `execution/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/execution/router.py` | classes: ExecutionRiskLimits, ExecutionRouter | Execution State Machine | router, integration boundary | 策略运行结果和运维可见 | `execution/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/execution/strategy_planner.py` | classes: StrategyExecutionPlan, EconomicExecutionPlan; funcs: plan_economic_intent, plan_strategy_intent | Execution State Machine | integration boundary, trading model consumer | 策略运行结果和运维可见 | `execution/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |

## `runtime/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/runtime/__init__.py` | package/export glue | Run Runtime | owner public surface | 运行配置/运维间接可见 | `runtime/` | Run 产品 owner 的当前导出入口；导出 RunKernel/RunProfile/RunRequest/RunResult/RunArtifactWriter、BoundRunProfile/runtime binding adapter、DurableOutboxCommandSubmitter、RuntimeRunLauncher、LiveRuntimeBindingConfig、LiveRuntimeComponents、LiveRunDaemon、运行组合契约和 service supervisor，不回到 `application/` 聚合。 |
| `kairospy/runtime/application.py` | classes: RuntimeStatus, ProbeResult, ReadinessProbe, FunctionProbe, PersistenceProbe, KairosApplication | Run Runtime | runtime application/readiness aggregate | 运行配置/运维可见 | `runtime/application.py` | 已承接旧 `application/runtime.py`；负责 runtime lifecycle status 和 readiness probe 组合，不是用户 surface，也不暴露给策略。 |
| `kairospy/runtime/async_runtime.py` | classes: AsyncKairosRuntime | Run Runtime | async run lifecycle wrapper | 运行配置/运维可见 | `runtime/async_runtime.py` | 已承接旧 `application/async_runtime.py`；只包装应用生命周期和 service supervisor，不承载策略业务规则或 connector DTO。 |
| `kairospy/runtime/bindings.py` | classes: EventSourceRunEventProvider, ExecutionPortCommandSubmitter, DurableOutboxCommandSubmitter, ExecutionRecoveryBinding, CompositeRecoveryBinding, ManagedServiceEvidenceProvider | Run Runtime | runtime binding adapter, port/service evidence bridge | 运行配置/运维间接可见 | `runtime/bindings.py` | 负责把有限 `market.stream.EventSource`、`integrations/ports` execution gateway、durable outbox dispatcher、execution recovery report、live recovery chain 和 supervisor snapshot 包装成 runtime binding evidence；不定义 connector capability model，不实现 provider SDK，不拥有 order state machine。 |
| `kairospy/runtime/clock.py` | classes: Clock, SystemClock, FixedClock | Run Runtime | clock contract | 运行配置/测试间接可见 | `runtime/clock.py` | 已承接旧 `application/clock.py`；提供 runtime 时钟端口，profile-specific replay clock 仍归 `runtime/profiles/backtest/clock.py`。 |
| `kairospy/runtime/composition.py` | classes: RunModeComposition, RuntimeFeedServicePlan, RuntimeFeedPlan, RuntimeFeedServiceBundle, RuntimeExecutionServicePlan, RuntimeExecutionPlan, RuntimeStrategyServicePlan, RuntimeStrategyPlan; funcs: backtest_composition, historical_simulation_composition, paper_trading_composition, live_composition, runtime_feed_plan, runtime_execution_plan, runtime_strategy_plan | Run Runtime | composition contract, service plan | 运行配置/运维间接可见 | `runtime/composition.py` | 已承接旧 `application/modes.py`；负责当前 run mode 组合和 feed/execution/strategy service plan 契约。后续 RunKernel/Profile 可以继续细分，但不能回流到 `application/`。 |
| `kairospy/runtime/config.py` | classes: RuntimePaths, ApplicationConfig | Run Runtime | runtime path/config contract | 运行配置/运维可见 | `runtime/config.py` | 已承接旧 `application/config.py`；只表达 runtime 所需路径和环境配置，不保存 credential，也不承担 workspace binding 语义。 |
| `kairospy/runtime/coordinator.py` | classes: PersistedOrderRecord, PersistedComboOrderRecord, PersistedCancellationRecord, ExecutionCoordinator | Run Runtime | execution coordination, durable runtime assembly | 运行配置/运维间接可见 | `runtime/coordinator.py` | 已承接旧 `orchestration/coordinator.py`；当前仍同时编排 execution/router/governance/runtime store，后续可继续把 order recovery 细节下沉到 execution。 |
| `kairospy/runtime/kernel.py` | classes: RunStatus, RunRequest, PreparedRun, SubmitResult, RecoveryResult, ProfileResult, RunArtifactLink, RunResult, RunProfile, RunArtifactWriter, IterableRunEventProvider, RunCommandSubmitterBinding, RuntimeRecoveryBinding, BoundRunProfile, RunKernel, StrategyRunResult, StrategyRunHooks, CanonicalBarMarketProjection, GovernedStrategyRunLoop | Run Runtime | run kernel contract, runtime-binding evidence, artifact-writer boundary, governed strategy loop | 运行配置/运维可见 | `runtime/kernel.py` | 已承接旧 `application/strategy_run_loop.py`；现在拥有 RunKernel/RunProfile 字段级 contract、profile dispatch、runtime binding evidence、artifact writer protocol 和 evidence hash 边界；StrategyRunResult 已携带 final Context `context_view_hashes`/`context_hash` 并在非空时纳入 audit hash；CanonicalBarMarketProjection 负责把 canonical bar event 的 source instance、event window、available_time、freshness 投影到 MarketView 可解释证据。不拥有 connector 实现、connector capability model、fill model、order state machine、ledger writer、artifact repository 或 pricing model。 |
| `kairospy/runtime/launch.py` | classes: RuntimeLaunchResult, RuntimeRunLauncher | Run Runtime | paper/live run launch use case, startup gate + managed service lifecycle + artifact evidence binding | 运行配置/运维可见 | `runtime/launch.py` | 负责把 KairosApplication startup gates、RunKernel、service evidence provider 或可选 managed services、artifact writer factory 串成启动用例；传入 `ManagedServiceSpec` 时会管理 `AsyncServiceSupervisor` 启停并在 artifact 写入前刷新 final service evidence；不拥有 governance repository、provider SDK、strategy implementation 或 order state machine。 |
| `kairospy/runtime/live_binding.py` | classes: LiveRuntimeComponents; funcs: bind_live_runtime_components | Run Runtime | live runtime component assembly, provider port binding to run evidence | 运行配置/运维可见 | `runtime/live_binding.py` | 负责把 LiveRuntimeBindingConfig、KairosApplication、runtime store、ReferenceCatalog、live market event source、execution/account/order-recovery port、durable outbox 和 recovery chain 组合成 BoundRunProfile；只接收 live environment 的 integrations/ports 实例，不发现 provider、不保存 credential、不定义 connector capability model、不进入 Strategy Context。 |
| `kairospy/runtime/live_config.py` | classes: LiveRuntimeBindingConfig | Run Runtime | live runtime evidence config, profile binding builder | 运行配置/运维可见 | `runtime/live_config.py` | 负责读取 `[runtime.live]` 的 data/strategy/config hash、readiness、promotion、account binding 和 recovery binding evidence，并转成 BoundRunProfile；不保存 credential，不实现 provider SDK，不定义 connector capability model，不进入 Strategy Context。 |
| `kairospy/runtime/live_daemon.py` | classes: LiveRunDaemonPhase, LiveRunDaemonSnapshot, LiveRunDaemon | Run Runtime | long-lived live session lifecycle, service evidence persistence | 运行配置/运维可见 | `runtime/live_daemon.py` | 负责长驻 live session 的 start/status/stop/recover/critical-fault contract，组合 KairosApplication gates、AsyncServiceSupervisor 和 runtime store evidence；不拥有 connector discovery、credential、strategy loop、governance artifact repository 或 order state machine。 |
| `kairospy/runtime/recovery.py` | classes: RuntimeRecoveryResult, RuntimeRecovery, RuntimeRecoveryService | Run Runtime | restart recovery service contract | 运维可见 | `runtime/recovery.py` | 已承接旧 `application/recovery.py`；负责 runtime restart readiness 和恢复汇总，reconciliation evidence 归 `governance/reconciliation.py`，execution order recovery 归 `execution/recovery.py`。 |
| `kairospy/runtime/service_supervisor.py` | classes: ServiceCriticality, ManagedServiceStatus, ManagedServiceSpec, ServiceFault, ManagedServiceSnapshot, AsyncServiceSupervisor | Run Runtime | async supervisor, service lifecycle | 运行配置/运维间接可见 | `runtime/service_supervisor.py` | 已承接旧 `application/service_supervisor.py`；负责 runtime 长生命周期 task 的状态、故障、重启和可观测快照。 |
| `kairospy/runtime/supervisor.py` | classes: RuntimeBackgroundService, RecoveryBackgroundService, SupervisorCycle, RuntimeSupervisor; funcs: write_soak_artifact | Run Runtime | runtime supervisor/cycle artifact writer | 运维可见 | `runtime/supervisor.py` | 已承接旧 `application/supervisor.py`；负责 supervisor cycle 编排和 soak artifact 写入，不承担 governance alert policy 或 connector 实现。 |
| `kairospy/runtime/store/__init__.py` | package/export glue | Run Runtime | runtime store namespace | 运行配置/运维间接可见 | `runtime/store/` | Runtime store 二级 owner 的导出入口；不替代 Data Product repository 或 Market Plane repository。 |
| `kairospy/runtime/store/event_log.py` | classes: PersistentEventLog | Run Runtime | persistent event log | 运行配置/运维间接可见 | `runtime/store/event_log.py` | 已承接旧 `orchestration/event_log.py`；负责 runtime 事件幂等日志，不承载治理判断。 |
| `kairospy/runtime/store/runtime_store.py` | classes: ManualOrderResolution, DurableExecutionRecord, SQLiteRuntimeStore | Run Runtime | durable runtime store | 运行配置/运维间接可见 | `runtime/store/runtime_store.py` | 已承接旧 `orchestration/runtime_store.py`；负责 run-local durable order/execution/ledger state，不作为 portfolio ledger 事实源。 |
| `kairospy/runtime/testing/__init__.py` | package/export glue | Run Runtime | runtime test harness namespace | 内部测试/演练 | `runtime/testing/` | Runtime failure drill 二级命名空间；不进入策略 API。 |
| `kairospy/runtime/testing/faults.py` | classes: RuntimeFaultPoint, RuntimeFaultInjector, InjectedRuntimeFailure, OneShotRuntimeFaultInjector; funcs: inject | Run Runtime | deterministic runtime fault injection | 内部测试/演练 | `runtime/testing/faults.py` | 已承接旧 `orchestration/faults.py`；用于 runtime recovery drill，不作为生产业务模型。 |
| `kairospy/runtime/profiles/__init__.py` | package/export glue | Run Runtime | profile namespace | 运行配置/运维间接可见 | `runtime/profiles/` | 三种 run profile 的命名空间；profile 是 mode 差异的唯一合法落点之一。 |
| `kairospy/runtime/profiles/backtest/__init__.py` | package/export glue, doc: Deterministic option backtesting. | BacktestProfile | profile public API | 回测用户可见结果 | `runtime/profiles/backtest/` | 已承接旧 `backtest/__init__.py`；导出 BacktestProfile adapter，顶层 `backtest/` 不再作为产品目录。 |
| `kairospy/runtime/profiles/backtest/clock.py` | classes: BacktestClock | BacktestProfile | replay clock | 回测用户可见结果 | `runtime/profiles/backtest/clock.py` | 已承接旧 `backtest/clock.py`；replay clock 是 BacktestProfile 专属假设，不能进入 live runtime kernel。 |
| `kairospy/runtime/profiles/backtest/engine.py` | classes: DeterministicIds, BacktestEngine | BacktestProfile | profile engine, deterministic run assembly | 回测用户可见结果 | `runtime/profiles/backtest/engine.py` | 已承接旧 `backtest/engine.py`；当前仍是回测组合中心，后续只可向 `runtime/kernel.py` 和 profile 内部 replay/fill/result 继续拆，不可成为 live kernel。 |
| `kairospy/runtime/profiles/backtest/execution.py` | classes: ComboQuote, ExecutionPlanner; funcs: combo_quote | BacktestProfile | deterministic execution planner | 回测用户可见结果 | `runtime/profiles/backtest/execution.py` | 已承接旧 `backtest/execution.py`；只表达回测成交规划辅助，不拥有通用 execution state machine。 |
| `kairospy/runtime/profiles/backtest/feed.py` | re-exports: SettlementType, InstrumentLifecycleSnapshot, MarketSnapshot, DatasetManifest, MarketReplayDataset, MarketSnapshotReplayFeed, MarketSnapshotFeed, build_manifest | BacktestProfile | thin profile entrypoint/re-export | 回测用户可见结果 | `runtime/profiles/backtest/feed.py` | 仅为 BacktestProfile 保留入口兼容/聚合；通用 historical snapshot/replay dataset contract 由 `market/snapshots.py` 拥有，不能在 profile 内继续增厚。 |
| `kairospy/runtime/profiles/backtest/fill.py` | classes: FillModelType, CommissionModel, FixedCommissionModel, FillAttempt, ListedOptionComboFillModel, SingleAssetOrder | BacktestProfile | deterministic fill model | 回测用户可见结果 | `runtime/profiles/backtest/fill.py` | 已承接旧 `backtest/fill.py`；回测 fill 假设不能泄漏到 simulation/live execution state machine。 |
| `kairospy/runtime/profiles/backtest/immediate.py` | classes: ImmediateBacktestPortfolio, ImmediateBacktestTrade, ImmediateIntentBacktestResult; funcs: run_immediate_target_backtest | BacktestProfile | immediate intent backtest helper | 回测用户可见结果 | `runtime/profiles/backtest/immediate.py` | 已承接旧 `application/immediate_backtest.py`；只提供 BacktestProfile 内的 deterministic intent 验证，不作为通用 execution 或 live runtime API。 |
| `kairospy/runtime/profiles/backtest/maker.py` | classes: BookEventType, IncrementalBookEvent, MakerOrderState, MakerEventResult, FifoMakerFillModel, HybridAction | BacktestProfile | maker fill simulation | 回测用户可见结果 | `runtime/profiles/backtest/maker.py` | 已承接旧 `backtest/maker.py`；用于回测/研究的 maker fill 模型，不替代真实订单生命周期。 |
| `kairospy/runtime/profiles/backtest/metrics.py` | funcs: calculate_metrics | BacktestProfile | performance metrics | 回测用户可见结果 | `runtime/profiles/backtest/metrics.py` | 已承接旧 `backtest/metrics.py`；回测指标产出 BacktestResult evidence，不进入 live readiness。 |
| `kairospy/runtime/profiles/backtest/portfolio.py` | classes: Position, StructurePosition, PositionSnapshot, PortfolioSnapshot, BacktestPortfolio | BacktestProfile | run-local portfolio adapter | 回测用户可见结果 | `runtime/profiles/backtest/portfolio.py` | 已承接旧 `backtest/portfolio.py`；这是 backtest-specific portfolio adapter，不是 portfolio owner 的账本事实源。 |
| `kairospy/runtime/profiles/backtest/profile.py` | classes: BacktestProfile; funcs: backtest_profile | BacktestProfile | RunProfile adapter contract | 回测用户可见结果 | `runtime/profiles/backtest/profile.py` | BacktestProfile 接入 RunProfile contract；prepare 校验 dataset/strategy/config/readiness，submit 不接真实 execution gateway，recovery policy 为 none；治理 artifact 必须能用统一 explain contract 还原 Context evidence。 |
| `kairospy/runtime/profiles/backtest/repository.py` | classes: BacktestRepository | BacktestProfile | result repository | 回测用户可见结果 | `runtime/profiles/backtest/repository.py` | 已承接旧 `backtest/repository.py`；只持久化 BacktestResult，不作为通用 runtime store。 |
| `kairospy/runtime/profiles/backtest/result.py` | classes: ResultStatus, BacktestConfig, EquityPoint, BacktestResult | BacktestProfile | result/artifact contract | 回测用户可见结果 | `runtime/profiles/backtest/result.py` | 已承接旧 `backtest/result.py`；BacktestResult 是 profile-specific artifact，后续需要与 RunArtifact 结构对齐。 |
| `kairospy/runtime/profiles/backtest/settlement.py` | funcs: intrinsic_value, due_settlements | BacktestProfile | deterministic settlement helper | 回测用户可见结果 | `runtime/profiles/backtest/settlement.py` | 已承接旧 `backtest/settlement.py`；只处理回测到期结算模拟，通用 product lifecycle 仍归 `products/`。 |
| `kairospy/runtime/profiles/backtest/synthetic_scenarios.py` | classes: SyntheticScenario, DatasetReadiness; funcs: build_synthetic_backtest_dataset, assess_dataset | BacktestProfile | synthetic replay dataset builder | 回测用户可见结果 | `runtime/profiles/backtest/synthetic_scenarios.py` | 已承接旧 `backtest/synthetic_scenarios.py`；用于 fixture/replay 验证，不作为 provider acquisition。 |
| `kairospy/runtime/profiles/simulation/__init__.py` | package/export glue | SimulationProfile | profile namespace | 运行配置/运维间接可见 | `runtime/profiles/simulation/` | SimulationProfile 二级命名空间；只承载 runtime rehearsal/profile contract，不提交真实风险账户订单。 |
| `kairospy/runtime/profiles/simulation/profile.py` | classes: SimulationProfile, SimulationMarketSource, SimulationExecutionBinding, SimulationClock; funcs: historical_replay_simulation_profile, paper_simulation_profile, exchange_testnet_simulation_profile | SimulationProfile | RunProfile adapter contract, readiness evidence | 运行配置/运维间接可见 | `runtime/profiles/simulation/profile.py` | 承接 historical replay、paper account、testnet 的非真实风险执行 profile，并已接入 RunProfile contract；依赖 governance readiness evidence，不拥有 fill model、connector SDK 或 order state machine；治理 artifact 必须能用统一 explain contract 还原 Context evidence。 |
| `kairospy/runtime/profiles/live/__init__.py` | package/export glue | LiveProfile | profile namespace | 运行配置/运维间接可见 | `runtime/profiles/live/` | LiveProfile 二级命名空间；导出 LiveProfile adapter，只承载 live profile 相关 evidence 和 adapter 组合，不替代 `integrations/connectors/`。 |
| `kairospy/runtime/profiles/live/profile.py` | classes: LiveProfile; funcs: live_profile | LiveProfile | RunProfile adapter contract, readiness/promotion gate | 运行配置/运维间接可见 | `runtime/profiles/live/profile.py` | LiveProfile 接入 RunProfile contract；prepare 校验 readiness、promotion、account binding 和 hash，未绑定真实 gateway/recovery 时 fail closed，不实现 connector 或 order state machine；进入策略循环后即使 profile status fail-closed，也必须写出同结构治理 artifact 和 Context evidence。 |
| `kairospy/runtime/profiles/live/reference_artifact.py` | consts: RUNTIME_REFERENCE_SCENARIO_ID; classes: RuntimeReferenceArtifactResult; funcs: run_runtime_reference_artifact | LiveProfile | deterministic live-runtime reference artifact | 运维/审计可见 | `runtime/profiles/live/reference_artifact.py` | 已承接旧 `application/runtime_reference_artifact.py`；用于验证 live runtime 链路证据，不进入策略 API，也不建立通用 connector capability domain。 |

## `application/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/application/__init__.py` | former package/export glue | former Application aggregate | package/export glue | 运行配置/运维间接可见 | 删除 | 已删除；最终目标不保留 `application/` 聚合入口，用户入口归 `surface/`，运行生命周期归 `runtime/`，治理证据归 `governance/`。 |
| `kairospy/application/async_runtime.py` | former classes: AsyncKairosRuntime | former Run Runtime source | async runtime wrapper | 运行配置/运维间接可见 | `runtime/async_runtime.py` | 已删除；当前入口是 `kairospy/runtime/async_runtime.py`。 |
| `kairospy/application/attribution.py` | former classes: SignalAttribution, PortfolioAttribution, ExecutionAttribution, RunAttribution; funcs: build_run_attribution | former Governance source | attribution evidence builder | 研究/审计/运维可见 | `governance/attribution.py` | 已删除；当前入口是 `kairospy/governance/attribution.py`。 |
| `kairospy/application/clock.py` | former classes: Clock, SystemClock, FixedClock | former Run Runtime source | clock contract | 运行配置/测试间接可见 | `runtime/clock.py` | 已删除；当前入口是 `kairospy/runtime/clock.py`。 |
| `kairospy/application/config.py` | former classes: RuntimePaths, ApplicationConfig | former Run Runtime source | runtime path/config contract | 运行配置/运维间接可见 | `runtime/config.py` | 已删除；当前入口是 `kairospy/runtime/config.py`。 |
| `kairospy/application/immediate_backtest.py` | former classes: ImmediateBacktestPortfolio, ImmediateBacktestTrade, ImmediateIntentBacktestResult; funcs: run_immediate_target_backtest | former BacktestProfile source | immediate intent backtest helper | 回测用户可见结果 | `runtime/profiles/backtest/immediate.py` | 已删除；当前入口是 `kairospy/runtime/profiles/backtest/immediate.py`。 |
| `kairospy/application/modes.py` | former classes: RunModeComposition, RuntimeFeedServicePlan, RuntimeFeedPlan, RuntimeFeedServiceBundle, RuntimeExecutionServicePlan, RuntimeExecutionPlan; funcs: backtest_composition, historical_simulation_composition, paper_trading_composition, live_composition, runtime_feed_plan, runtime_execution_plan | former Run Runtime composition source | service, runtime | 运行配置/运维间接可见 | `runtime/composition.py` | 已删除；当前入口是 `kairospy/runtime/composition.py` 和 `kairospy.runtime`。 |
| `kairospy/application/recovery.py` | former classes: RuntimeRecoveryResult, RuntimeRecovery, RuntimeRecoveryService | former Run Runtime source | restart recovery service contract | 运维可见 | `runtime/recovery.py` | 已删除；当前入口是 `kairospy/runtime/recovery.py`，reconciliation evidence 归 `governance/reconciliation.py`。 |
| `kairospy/application/run_artifact.py` | former classes: RunArtifact, RunArtifactRepository | former Governance source | run artifact repository/evidence | 审计/运维可见 | `governance/artifact.py` | 已删除；当前入口是 `kairospy/governance/artifact.py`。 |
| `kairospy/application/runtime.py` | former classes: RuntimeStatus, ProbeResult, ReadinessProbe, FunctionProbe, PersistenceProbe, KairosApplication | former Run Runtime source | runtime application/readiness aggregate | 运行配置/运维间接可见 | `runtime/application.py` | 已删除；当前入口是 `kairospy/runtime/application.py`。 |
| `kairospy/application/runtime_failure_policy.py` | former funcs: run_runtime_failure_policy | former Governance source | deterministic incident/failure policy artifact | 运维/审计可见 | `governance/incidents.py` | 已删除；当前入口是 `kairospy/governance/incidents.py`。 |
| `kairospy/application/runtime_reference_artifact.py` | former classes: RuntimeReferenceArtifactResult; funcs: run_runtime_reference_artifact | former LiveProfile source | deterministic live-runtime reference artifact | 运维/审计可见 | `runtime/profiles/live/reference_artifact.py` | 已删除；当前入口是 `kairospy/runtime/profiles/live/reference_artifact.py`。 |
| `kairospy/application/service_supervisor.py` | former classes: ServiceCriticality, ManagedServiceStatus, ManagedServiceSpec, ServiceFault, ManagedServiceSnapshot, AsyncServiceSupervisor | former Run Runtime supervisor source | service, supervisor | 运行配置/运维间接可见 | `runtime/service_supervisor.py` | 已删除；当前入口是 `kairospy/runtime/service_supervisor.py` 和 `kairospy.runtime`。 |
| `kairospy/application/strategy_run_loop.py` | former classes: StrategyRunResult, StrategyRunHooks, CanonicalBarMarketProjection, GovernedStrategyRunLoop | former Run Runtime source | governed strategy loop/kernel source | 运行配置/运维间接可见 | `runtime/kernel.py` | 已删除；当前入口是 `kairospy/runtime/kernel.py`。 |
| `kairospy/application/supervisor.py` | former classes: RuntimeBackgroundService, RecoveryBackgroundService, SupervisorCycle, RuntimeSupervisor; funcs: write_soak_artifact | former Run Runtime source | runtime supervisor/cycle artifact writer | 运维可见 | `runtime/supervisor.py` | 已删除；当前入口是 `kairospy/runtime/supervisor.py`。 |

## `backtest/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/backtest/__init__.py` | former doc: Deterministic option backtesting. | former BacktestProfile package API | package/export glue | 回测用户可见结果 | `runtime/profiles/backtest/__init__.py` | 已删除；当前入口是 `kairospy/runtime/profiles/backtest/`，不保留顶层 `backtest/` 产品目录。 |
| `kairospy/backtest/calendar.py` | former classes: TradingSession, TradingCalendar, AlwaysOpenCalendar, CalendarRegistry; funcs: us_market_holidays, us_market_early_closes | former Run Runtime calendar source | calendar/session contract | 回测用户可见结果 | `products/common/calendars.py` | 已删除；交易日历是产品公共日历能力，不是 BacktestProfile 专属能力。 |
| `kairospy/backtest/clock.py` | former classes: BacktestClock | former BacktestProfile clock source | replay clock | 回测用户可见结果 | `runtime/profiles/backtest/clock.py` | 已删除；当前入口是 `runtime/profiles/backtest/clock.py`。 |
| `kairospy/backtest/engine.py` | former classes: DeterministicIds, BacktestEngine | former BacktestProfile engine source | engine, persistence, risk-coupled, trading model consumer | 回测用户可见结果 | `runtime/profiles/backtest/engine.py` | 已删除；当前入口是 `runtime/profiles/backtest/engine.py`，后续只可继续拆入 runtime kernel/profile，不可回到顶层。 |
| `kairospy/backtest/execution.py` | former classes: ComboQuote, ExecutionPlanner; funcs: combo_quote | former BacktestProfile execution planner source | planner, trading model consumer | 回测用户可见结果 | `runtime/profiles/backtest/execution.py` | 已删除；当前入口是 `runtime/profiles/backtest/execution.py`。 |
| `kairospy/backtest/feed.py` | former classes: SettlementType, InstrumentLifecycleSnapshot, MarketSnapshot, DatasetManifest, MarketReplayDataset, MarketSnapshotReplayFeed; funcs: build_manifest | former BacktestProfile replay feed source | persistence, trading model consumer | 回测用户可见结果 | `runtime/profiles/backtest/feed.py` | 已删除；当前入口是 `runtime/profiles/backtest/feed.py`。 |
| `kairospy/backtest/fill.py` | former classes: FillModelType, CommissionModel, FixedCommissionModel, FillAttempt, ListedOptionComboFillModel, SingleAssetOrder | former BacktestProfile fill source | deterministic fill model | 回测用户可见结果 | `runtime/profiles/backtest/fill.py` | 已删除；当前入口是 `runtime/profiles/backtest/fill.py`。 |
| `kairospy/backtest/maker.py` | former classes: BookEventType, IncrementalBookEvent, MakerOrderState, MakerEventResult, FifoMakerFillModel, HybridAction | former BacktestProfile maker fill source | decision, trading model consumer | 回测用户可见结果 | `runtime/profiles/backtest/maker.py` | 已删除；当前入口是 `runtime/profiles/backtest/maker.py`。 |
| `kairospy/backtest/metrics.py` | former funcs: calculate_metrics | former BacktestProfile metrics source | backtest-coupled, trading model consumer | 回测用户可见结果 | `runtime/profiles/backtest/metrics.py` | 已删除；当前入口是 `runtime/profiles/backtest/metrics.py`。 |
| `kairospy/backtest/portfolio.py` | former classes: Position, StructurePosition, PositionSnapshot, PortfolioSnapshot, BacktestPortfolio | former BacktestProfile portfolio adapter source | risk-coupled, trading model consumer | 回测用户可见结果 | `runtime/profiles/backtest/portfolio.py` | 已删除；当前入口是 `runtime/profiles/backtest/portfolio.py`。 |
| `kairospy/backtest/repository.py` | former classes: BacktestRepository | former BacktestProfile repository source | repository, persistence | 回测用户可见结果 | `runtime/profiles/backtest/repository.py` | 已删除；当前入口是 `runtime/profiles/backtest/repository.py`。 |
| `kairospy/backtest/result.py` | former classes: ResultStatus, BacktestConfig, EquityPoint, BacktestResult | former BacktestProfile result source | backtest-coupled, risk-coupled, trading model consumer | 回测用户可见结果 | `runtime/profiles/backtest/result.py` | 已删除；当前入口是 `runtime/profiles/backtest/result.py`。 |
| `kairospy/backtest/settlement.py` | former funcs: intrinsic_value, due_settlements | former BacktestProfile settlement source | trading model consumer | 回测用户可见结果 | `runtime/profiles/backtest/settlement.py` | 已删除；当前入口是 `runtime/profiles/backtest/settlement.py`。 |
| `kairospy/backtest/synthetic_scenarios.py` | former classes: SyntheticScenario, DatasetReadiness; funcs: build_synthetic_backtest_dataset, assess_dataset | former BacktestProfile synthetic scenario source | trading model consumer | 回测用户可见结果 | `runtime/profiles/backtest/synthetic_scenarios.py` | 已删除；当前入口是 `runtime/profiles/backtest/synthetic_scenarios.py`。 |

## `research/capture/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/research/capture/__init__.py` | doc: Capture, snapshot, and option-series helpers. | Research/Validation | model/helpers | 研究员工作流 | `research/capture/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/research/capture/data_store.py` | classes: CollectionSession, CollectionManifest, MarketSnapshotCollectionPublisher; funcs: merge_datasets | Research/Validation | research snapshot collection publisher | 研究员工作流 | `research/capture/` | 负责研究采集 session/manifest 和 MarketSnapshot dataset 合并发布；dataset contract 来自 `market/snapshots.py`，不依赖 BacktestProfile。 |
| `kairospy/research/capture/features.py` | classes: FeatureSnapshot, FeatureEngine; funcs: build_features | Research/Validation | engine | 研究员工作流 | `research/capture/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/research/capture/normalized_series.py` | classes: NormalizedQuoteProvider, NormalizedSeriesCaptureService | Research/Validation | normalized series capture service, market snapshot builder | 研究员工作流 | `research/capture/` | 负责把 normalized quote provider 产出为 MarketSnapshot dataset；snapshot/manifest contract 来自 `market/snapshots.py`，不依赖 BacktestProfile。 |
| `kairospy/research/capture/option_capture.py` | classes: OptionCaptureService | Research/Validation | service, integration boundary, persistence, trading model consumer | 研究员工作流 | `research/capture/` | 依赖 connector；目标需要改为 integrations port contract + readiness evidence。 |
| `kairospy/research/capture/option_snapshot_analysis.py` | classes: OptionSnapshotMetricRow, OptionSnapshotAnalysis, IvSmilePoint, PutCallPair; funcs: analyze_option_snapshot | Research/Validation | trading model consumer | 研究员工作流 | `research/capture/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/research/capture/option_universe_selector.py` | funcs: select_expirations, select_strikes, select_instruments | Research/Validation | trading model consumer | 研究员工作流 | `research/capture/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/research/capture/report.py` | funcs: write_csv, summarize | Research/Validation | model/helpers | 研究员工作流 | `research/capture/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/research/capture/retention.py` | classes: RetainedLeg, RetentionManifest, DeltaLegWatchlist | Research/Validation | retention/watchlist model, market snapshot consumer | 研究员工作流 | `research/capture/` | 消费 `market/snapshots.py` 的 MarketSnapshot 计算保留腿/watchlist；不依赖 BacktestProfile。 |
| `kairospy/research/capture/series.py` | classes: SeriesCaptureSpec, SeriesCaptureProgress, SeriesCaptureService | Research/Validation | series capture service, integration boundary, market snapshot builder | 研究员工作流 | `research/capture/` | MarketSnapshot/manifest contract 已来自 `market/snapshots.py`，不依赖 BacktestProfile；仍直接使用 provider client，后续需要改为 integrations port contract + readiness evidence。 |
| `kairospy/research/capture/snapshot.py` | classes: DataQualityIssue, InstrumentSnapshot, OptionCaptureSnapshot, ReferenceSnapshotEvidence; funcs: build_reference_evidence, build_snapshot | Research/Validation | trading model consumer | 研究员工作流 | `research/capture/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/research/capture/spec.py` | classes: MarketDataType, OptionChainCaptureSpec | Research/Validation | trading model consumer | 研究员工作流 | `research/capture/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/research/capture/tutorial_data.py` | funcs: tutorial_sma_bars, ensure_sma_tutorial_dataset | Research/Validation | persistence, trading model consumer | 研究员工作流 | `research/capture/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |

## `research/validation/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/research/validation/__init__.py` | doc: Governed validation contracts and gates. | Research/Validation | public owner API | 研究员工作流 | `research/validation/` | 导出研究验证 contract、gate、claim、统计和报告能力；不导出 governance audit。 |
| `kairospy/research/validation/artifacts.py` | classes: ValidationArtifactWriter | Research/Validation | validation artifact writer | 研究员工作流 | `research/validation/artifacts.py` | 写 experiment validation artifact，不审计运行目录，也不决定 live promotion。 |
| `kairospy/research/validation/bootstrap.py` | funcs: block_bootstrap_mean_ci, newey_west_mean_t | Research/Validation | statistical helper | 研究员工作流 | `research/validation/bootstrap.py` | 研究统计 primitive；不进入 runtime path。 |
| `kairospy/research/validation/claims.py` | classes: ClaimDecision; funcs: authorize_claim | Research/Validation | research claim gate | 研究员工作流 | `research/validation/claims.py` | 只约束研究结论可以声称什么；live readiness/promotion 归 governance。 |
| `kairospy/research/validation/contracts.py` | classes: ValidationLevel, EvidenceStatus, ProductProtocol, ReturnDriver, ExecutionArchetype, OutOfSampleEvidence | Research/Validation | validation contract model | 研究员工作流 | `research/validation/contracts.py` | 研究验证事实和等级模型；不复用为 runtime state。 |
| `kairospy/research/validation/data_gaps.py` | funcs: build_data_gap_plan | Research/Validation | data gap planner | 研究员工作流 | `research/validation/data_gaps.py` | 研究数据缺口计划，不直接启动 data acquisition job。 |
| `kairospy/research/validation/gates.py` | classes: GateRequirement, GateDecision, ValidationGate | Research/Validation | validation gate | 研究员工作流 | `research/validation/gates.py` | 研究验证 gate；不能替代 live readiness gate 或 kill switch。 |
| `kairospy/research/validation/predictability.py` | classes: PredictabilityResult; funcs: validate_predictability | Research/Validation | signal predictability check | 研究员工作流 | `research/validation/predictability.py` | 信号可预测性统计，不生成策略 intent。 |
| `kairospy/research/validation/protocols.py` | classes: ProtocolDecision; funcs: validate_product_protocol, validate_return_driver_protocol | Research/Validation | validation protocol check | 研究员工作流 | `research/validation/protocols.py` | 验证研究协议所需数据能力，不接触 connector。 |
| `kairospy/research/validation/report.py` | funcs: render_validation_report | Research/Validation | report renderer | 研究员工作流 | `research/validation/report.py` | 研究验证报告，不写 run artifact。 |
| `kairospy/research/validation/robustness.py` | classes: RobustnessResult; funcs: assess_robustness | Research/Validation | robustness check | 研究员工作流 | `research/validation/robustness.py` | 鲁棒性统计，不决定实盘仓位。 |
| `kairospy/research/validation/samples.py` | funcs: overlap_adjusted_effective_samples, approximate_required_samples, assess_sample_sufficiency | Research/Validation | sample sufficiency check | 研究员工作流 | `research/validation/samples.py` | 样本充分性统计，不读取 provider。 |
| `kairospy/research/validation/split.py` | classes: TimeSplit; funcs: chronological_split, walk_forward_splits | Research/Validation | time split helper | 研究员工作流 | `research/validation/split.py` | 研究切分 primitive，不作为 backtest profile。 |
| `kairospy/research/validation/test_windows.py` | classes: TestWindowUse, TestWindowRegistry | Research/Validation | global test window registry | 研究员工作流 | `research/validation/test_windows.py` | 管理研究 test window 占用，避免泄漏到 live runtime。 |

## former `capture/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/capture/` | former research sample capture package | Research/Validation | legacy top-level package | 研究员工作流 | `research/capture/` | 已删除；研究样本捕获不能作为一级产品目录回流。 |

## former `validation/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/validation/` | former governed validation package | Research/Validation + Governance/Operations | legacy top-level package | 研究员/审计工作流 | `research/validation/` + `governance/audit.py` | 已删除；研究验证归 research，治理审计归 governance。 |
| `kairospy/validation/audit.py` | former classes: GovernanceAudit; funcs: audit_governance | Governance/Operations | former governance audit source | 审计/运维可见 | `governance/audit.py` | 已删除；audit_governance 已收口到 governance。 |

## `orchestration/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/orchestration/__init__.py` | former package/export glue | 删除的组合入口 | model/helpers | 运行配置/运维间接可见 | 删除；职责拆到 `runtime/`、`execution/`、`governance/`、`integrations/` | 已删除；最终目标不保留 orchestration 聚合入口。 |
| `kairospy/orchestration/coordinator.py` | former classes: PersistedOrderRecord, PersistedComboOrderRecord, PersistedCancellationRecord, ExecutionCoordinator | former runtime coordinator source | integration boundary, persistence, trading model consumer | 运行配置/运维间接可见 | `runtime/coordinator.py` | 已删除；当前入口是 `runtime/coordinator.py`，后续 execution recovery 细节可继续下沉到 execution。 |
| `kairospy/orchestration/event_log.py` | former classes: PersistentEventLog | former runtime event log source | persistence | 运行配置/运维间接可见 | `runtime/store/event_log.py` | 已删除；当前入口是 `runtime/store/event_log.py`。 |
| `kairospy/orchestration/faults.py` | former classes: RuntimeFaultPoint, RuntimeFaultInjector, InjectedRuntimeFailure, OneShotRuntimeFaultInjector; funcs: inject | former runtime testing source | runtime fault drill | 运行配置/运维间接可见 | `runtime/testing/faults.py` | 已删除；当前入口是 `runtime/testing/faults.py`。 |
| `kairospy/orchestration/kill_switch.py` | former classes: KillSwitchResult, KillSwitch | former governance control source | integration boundary, trading model consumer | 内部实现 | `governance/kill_switch.py` | 已删除；当前入口是 `governance/kill_switch.py`。 |
| `kairospy/orchestration/monitoring.py` | former classes: AlertSeverity, OperationalAlert, OperationalMonitor | former governance observability source | model/helpers | 内部实现 | `governance/observability.py` | 已删除；当前入口是 `governance/observability.py`。 |
| `kairospy/orchestration/reconciliation.py` | former classes: ReconciliationDifference, ReconciliationReport, ReconciliationService | former governance reconciliation source | service, report, integration boundary, risk-coupled | 内部实现 | `governance/reconciliation.py` | 已删除；当前入口是 `governance/reconciliation.py`。 |
| `kairospy/orchestration/runtime_store.py` | former classes: ManualOrderResolution, DurableExecutionRecord, SQLiteRuntimeStore | former runtime store source | store, runtime, integration boundary, persistence | 运行配置/运维间接可见 | `runtime/store/runtime_store.py` | 已删除；当前入口是 `runtime/store/runtime_store.py`。 |
| `kairospy/orchestration/strategy_monitoring.py` | former classes: StrategyHealth, StrategyMonitoringLimits, StrategyMonitoringSnapshot, StrategyHealthDecision, StrategyHealthMonitor | former governance strategy monitoring source | decision | 内部实现 | `governance/strategy_monitoring.py` | 已删除；当前入口是 `governance/strategy_monitoring.py`。 |

## `integrations/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/integrations/live_ports.py` | classes: LiveProviderPorts, LiveMarketEventSourceBinding; funcs: build_live_provider_ports, build_live_market_event_source, parse_account_ref | External Integrations | live provider port factory, live market EventSource factory, explicit provider/market binding resolver | 运行配置/运维间接可见 | `integrations/live_ports.py` | 负责把显式 live provider binding、reference catalog 和项目 credential reference 转成 execution/account/order-recovery port 实例，并把 Data Product Live View / provider runtime feed 转成 market EventSource channel；不生成 RunProfile，不做 readiness/promotion 决策，不定义 connector capability model，也不进入 Strategy Context。 |

## `integrations/connectors/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/integrations/connectors/__init__.py` | doc: External system connectors for market data, reference data, execution, and transfers. | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/artifacts.py` | classes: ProviderEstimate, SourceArtifact, ProviderEvent, ProviderHealth | External Integrations | artifact | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/binance/__init__.py` | package/export glue | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/binance/account_gateway.py` | classes: BinanceAccountGateway, BinanceOptionsAccountGateway | External Integrations | external gateway, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/binance/datasets.py` | classes: BinanceSpotDatasetConnector, BinanceUsdmPerpetualHourlyDatasetConnector, BinanceOptionQuotesDatasetConnector | External Integrations | persistence | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/binance/execution_gateway.py` | classes: BinanceExecutionGateway, BinanceOptionsExecutionGateway | External Integrations | external gateway, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/binance/funding_ingestion.py` | classes: FundingBackfillReport, BinanceDurableFundingBackfill | External Integrations | report, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/binance/funding_settlement.py` | classes: BinanceFundingSettlementClient | External Integrations | external client, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/binance/historical_archive.py` | classes: BinanceSpotArchiveProvider, BinanceUsdmPerpetualHourlyArchiveProvider, GracefulShutdown | External Integrations | persistence | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/binance/market_data_client.py` | classes: BinanceMarketDataClient; doc: Binance REST market data snapshot client. | External Integrations | external client, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/binance/market_stream.py` | classes: WebSocketConnection, WebSocketConnector, WebSocketClientConnection, WebSocketClientConnector, BinanceStreamSession; funcs: websocket_url, parse_market_stream_event; doc: Binance public market stream utilities and reconnecting stream sessions. | External Integrations | external client, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/binance/option_market_snapshot.py` | classes: OptionMarketSnapshot; funcs: parse_option_market_snapshot | External Integrations | trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/binance/options_archive.py` | classes: BinanceOptionsEohArchiveProvider; funcs: normalize_eoh_rows | External Integrations | persistence | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/binance/order_book.py` | classes: OrderBookSnapshotProvider, BinanceOrderBookSyncFault, BinanceOrderBookSnapshotProvider, BinanceOrderBookSyncMetrics, BinanceOrderBookSyncService | External Integrations | service, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/binance/order_recovery.py` | classes: RecoverySnapshot, BinanceRecoveryService | External Integrations | service, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/binance/reference_data.py` | classes: BinanceSpotReferenceDataClient, BinanceFuturesReferenceDataClient, BinanceOptionsReferenceDataClient; doc: Binance reference data clients and product definition builders. | External Integrations | external client, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/binance/request_signing.py` | classes: BinanceSigner; funcs: synchronize_clock; doc: Binance request signing and clock synchronization. | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/binance/rest_transport.py` | classes: BinanceTransport, UrllibBinanceTransport, RateLimiter; doc: Binance REST transport protocol, urllib implementation, and rate limiter. | External Integrations | transport | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/binance/runtime_feed.py` | classes: BinanceRuntimeFeed, BinanceRuntimeFeedFactory | External Integrations | runtime, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/binance/stream.py` | classes: BinanceCanonicalStreamService | External Integrations | service, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/binance/user_data_stream.py` | classes: UserFillUpdate, BalanceUpdate, BinanceUserDataStreamService, BinanceUserStreamProcessor; funcs: parse_user_stream_event | External Integrations | service, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/codecs.py` | classes: ProviderCodec | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/data_planes.py` | classes: DataPlaneEndpoint, ProviderDataPlaneSpec, ProviderDataPlane | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/deribit/__init__.py` | package/export glue | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/deribit/datasets.py` | classes: DeribitDvolDatasetConnector, DeribitOptionTradesDatasetConnector, DeribitOptionSnapshotDatasetConnector | External Integrations | persistence | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/deribit/historical.py` | classes: DeribitDvolProvider | External Integrations | persistence | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/deribit/option_chain.py` | classes: DeribitOptionChainProvider; funcs: normalize_chain | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/deribit/trade_history.py` | classes: DeribitOptionTradeHistoryProvider; funcs: normalize_deribit_trades | External Integrations | persistence | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/execution.py` | classes: ExecutionService, ComboExecutionService, ExecutionServiceSpec | External Integrations | service, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/ibkr/__init__.py` | package/export glue | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/ibkr/account_gateway.py` | classes: IbkrAccountGateway | External Integrations | external gateway, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/ibkr/execution_gateway.py` | classes: IbkrExecutionGateway; funcs: normalize_ibkr_execution | External Integrations | external gateway, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/ibkr/ingestion.py` | classes: IbkrDurableFillIngestion | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/ibkr/market_data_client.py` | classes: IbkrMarketDataClient | External Integrations | external client, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/ibkr/option_chain_provider.py` | classes: SpxwOptionChainProvider, IbkrSpxwOptionChainProvider; funcs: decimal_or_none | External Integrations | trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/ibkr/reference_data.py` | classes: IbkrReferenceDataClient | External Integrations | external client, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/ibkr/session.py` | classes: IbkrSession | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/market_data_router.py` | classes: CompositeMarketDataClient | External Integrations | external client, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/massive/__init__.py` | package/export glue | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/massive/client.py` | classes: MassiveError, MassiveResponse, MassiveTransport, UrllibMassiveTransport, MassiveClient; funcs: redact_url | External Integrations | external client, transport | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/massive/close_implied_volatility.py` | classes: OptionCloseImpliedVolatilityPipeline | External Integrations | persistence, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/massive/config.py` | classes: MassiveConfig | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/massive/corporate_actions.py` | classes: MassiveCorporateActionDecoder | External Integrations | trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/massive/curated.py` | classes: MassiveMarketSnapshotBuilder | External Integrations | provider-curated market snapshot builder | provider 接入/诊断间接可见 | `integrations/connectors/` | Massive provider 的 curated MarketSnapshot dataset builder；snapshot/manifest contract 来自 `market/snapshots.py`，不依赖 BacktestProfile。 |
| `kairospy/integrations/connectors/massive/daily_ohlcv.py` | classes: OpraInventoryEntry, OptionDailyOhlcvPipeline, SpxwDailyOhlcvPipeline | External Integrations | persistence, backtest-coupled | provider 接入/诊断间接可见 | `integrations/connectors/` | 依赖 backtest；目标需要切断运行模式泄漏。 |
| `kairospy/integrations/connectors/massive/datasets.py` | classes: MassiveOptionProductConfig, MassiveEquityDailyOhlcvProductConfig, MassiveEquityDailyOhlcvDatasetConnector, MassiveEquityDailyMarketOhlcvDatasetConnector, MassiveEquityHourlyOhlcvDatasetConnector, MassiveOptionEventsDatasetConnector | External Integrations | persistence, backtest-coupled | provider 接入/诊断间接可见 | `integrations/connectors/` | 依赖 backtest；目标需要切断运行模式泄漏。 |
| `kairospy/integrations/connectors/massive/decoder.py` | funcs: decode_quotes, decode_trades, decode_option_snapshots, decode_bars | External Integrations | trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/massive/entitlement_diagnostics.py` | classes: MassiveEntitlementReport, MassiveEntitlementDiagnostics | External Integrations | report | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/massive/equity_daily_ohlcv.py` | classes: MassiveEquityDailyOhlcvPipeline, MassiveEquityHourlyOhlcvPipeline | External Integrations | persistence, backtest-coupled | provider 接入/诊断间接可见 | `integrations/connectors/` | 依赖 backtest；目标需要切断运行模式泄漏。 |
| `kairospy/integrations/connectors/massive/equity_identity.py` | classes: MassiveEquityIdentityResult, MassiveEquityIdentityResolver | External Integrations | persistence, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/massive/market_data.py` | classes: MassiveAggregateBarsRequest, MassiveAggregateBarsArtifact, MassiveAggregateBarsResource, MassiveHistoricalMarketDataService | External Integrations | service, artifact, integration boundary | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/massive/pipeline.py` | classes: MassiveOptionDataPipeline | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/massive/reference.py` | classes: MassiveReferenceImporter | External Integrations | backtest-coupled, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 依赖 backtest；目标需要切断运行模式泄漏。 |
| `kairospy/integrations/connectors/massive/reference_pipeline.py` | classes: MassiveReferencePipeline | External Integrations | persistence | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/massive/reference_store.py` | classes: MassiveReferenceStore | External Integrations | store, persistence | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/massive/vendor_archive.py` | classes: OutsideDownloadWindow, ArchivedRequest, MassiveVendorArchiveClient, MassiveFlatFileClient, MassiveFlatFileBatchDownloader; funcs: request_fingerprint | External Integrations | external client, persistence, backtest-coupled | provider 接入/诊断间接可见 | `integrations/connectors/` | 依赖 backtest；目标需要切断运行模式泄漏。 |
| `kairospy/integrations/connectors/massive/websocket.py` | classes: MassiveWebSocketClient, MassiveLiveStream, MassiveStreamFault, MassiveCanonicalStreamService | External Integrations | external client, service | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/provider_contracts.py` | classes: ProviderConnector | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/resources.py` | classes: ProviderResource, ProviderResourceSpec | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/services.py` | classes: ProviderService, HistoricalMarketDataService, ProviderServiceSpec | External Integrations | service | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/simulated.py` | classes: SimulatedExecutionAccountGateway | External Integrations | external gateway, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/transfer/__init__.py` | package/export glue | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/transfer/bank.py` | classes: BankTransferProviderClient, BankTransferGateway | External Integrations | external gateway, external client, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/transfer/binance.py` | classes: BinanceWalletRoute, BinanceTransferGateway | External Integrations | external gateway, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/connectors/transports.py` | classes: TransportRequest, TransportResponse, ProviderTransport | External Integrations | transport | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |

## `integrations/ports/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/integrations/ports/__init__.py` | package/export glue | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/ports/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/ports/account.py` | package/export glue | External Integrations | integration boundary | provider 接入/诊断间接可见 | `integrations/ports/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/ports/execution.py` | package/export glue | External Integrations | integration boundary | provider 接入/诊断间接可见 | `integrations/ports/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/ports/market_data.py` | package/export glue | External Integrations | integration boundary | provider 接入/诊断间接可见 | `integrations/ports/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/ports/reference_data.py` | package/export glue | External Integrations | integration boundary | provider 接入/诊断间接可见 | `integrations/ports/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/ports/venue.py` | classes: Environment, VenueOrderStatus, OrderRequest, OrderAck, VenueBalance, ComboLegRequest | External Integrations | trading model consumer | provider 接入/诊断间接可见 | `integrations/ports/` | 当前把 account/execution/reference 多种 port 契约混合；目标拆到 integrations/ports/*。 |

## `integrations/contracts/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/integrations/contracts/__init__.py` | doc: Versioned, transport-independent contracts shared by runtime components. | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/contracts/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/integrations/contracts/market_data.py` | classes: MarketEventKind, QuotePayload, TradePayload, BarPayload, OrderBookLevelPayload, OrderBookDeltaPayload; funcs: canonicalize_market_event, canonical_from_trading_market_data | External Integrations | trading model consumer | provider 接入/诊断间接可见 | `integrations/contracts/` | 外部 canonical market contract；目标可能是 integrations/contracts 或 market/events，取决于是否是 provider-neutral envelope 还是运行时事件。 |

## `infrastructure/storage/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/infrastructure/storage/__init__.py` | doc: File-backed capture and artifact persistence. | Infrastructure | public owner API | 内部实现 | `infrastructure/storage/` | 已承接旧 `storage/__init__.py`；只导出基础设施持久化入口，不成为产品域。 |
| `kairospy/infrastructure/storage/codec.py` | funcs: to_primitive, from_primitive, snapshot_from_primitive, snapshot_to_primitive, restore_primitives, event_to_primitive | Infrastructure | codec/serialization helper | 内部实现 | `infrastructure/storage/codec.py` | 已承接旧 `storage/codec.py`；只处理 dataclass/value object 序列化，不承载业务规则或策略视图。 |
| `kairospy/infrastructure/storage/data_lake.py` | funcs: sha256_bytes, write_json, utc_midnight, write_daily_dataset, write_intraday_dataset, write_event_dataset | Infrastructure | file-backed dataset writer | 内部实现 | `infrastructure/storage/data_lake.py` | 已承接旧 `storage/data_lake.py`；只交付物理 dataset 写入 primitive，不拥有 Data Product 语义。 |
| `kairospy/infrastructure/storage/repository.py` | classes: RunStatus, RunManifest, FileOptionCaptureRepository; funcs: new_manifest | Infrastructure | file-backed repository | 内部实现 | `infrastructure/storage/repository.py` | 已承接旧 `storage/repository.py`；历史上偏 option capture，后续应由 capture/research owner 调用，不能作为顶层产品入口。 |

## former `storage/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/storage/__init__.py` | former doc: File-backed capture and artifact persistence. | Infrastructure | legacy top-level package | 内部实现 | `infrastructure/storage/__init__.py` | 已删除；基础设施能力不能作为一级产品目录回流。 |
| `kairospy/storage/codec.py` | former funcs: to_primitive, from_primitive, snapshot_from_primitive, snapshot_to_primitive, restore_primitives, event_to_primitive | Infrastructure | former codec source | 内部实现 | `infrastructure/storage/codec.py` | 已删除；codec 已收口到 infrastructure/storage。 |
| `kairospy/storage/data_lake.py` | former funcs: sha256_bytes, write_json, utc_midnight, write_daily_dataset, write_intraday_dataset, write_event_dataset | Infrastructure | former data lake writer source | 内部实现 | `infrastructure/storage/data_lake.py` | 已删除；data lake primitive 已收口到 infrastructure/storage。 |
| `kairospy/storage/repository.py` | former classes: RunStatus, RunManifest, FileOptionCaptureRepository; funcs: new_manifest | Infrastructure | former file repository source | 内部实现 | `infrastructure/storage/repository.py` | 已删除；file-backed repository 已收口到 infrastructure/storage。 |

## `portfolio/treasury/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/portfolio/treasury/__init__.py` | doc: Asset movement planning, execution state and ledger coordination. | Portfolio/Account State | model/helpers | 策略运行结果和运维可见 | `portfolio/treasury/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/portfolio/treasury/accounting.py` | classes: TreasuryAccountingProjector | Portfolio/Account State | trading model consumer | 策略运行结果和运维可见 | `portfolio/treasury/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/portfolio/treasury/coordinator.py` | classes: TreasuryCoordinator | Portfolio/Account State | model/helpers | 策略运行结果和运维可见 | `portfolio/treasury/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/portfolio/treasury/ledger_posting.py` | classes: TreasuryLedgerPostingService | Portfolio/Account State | service, trading model consumer | 策略运行结果和运维可见 | `portfolio/treasury/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/portfolio/treasury/planner.py` | classes: TreasuryPlanner | Portfolio/Account State | planner | 策略运行结果和运维可见 | `portfolio/treasury/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/portfolio/treasury/policy.py` | classes: TransferPolicy | Portfolio/Account State | policy, trading model consumer | 策略运行结果和运维可见 | `portfolio/treasury/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/portfolio/treasury/reconciliation.py` | classes: TransferObservation, TransferReconciliationService | Portfolio/Account State | service, trading model consumer | 策略运行结果和运维可见 | `portfolio/treasury/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/portfolio/treasury/repository.py` | classes: SQLiteTreasuryRepository | Portfolio/Account State | repository, trading model consumer | 策略运行结果和运维可见 | `portfolio/treasury/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/portfolio/treasury/state_machine.py` | classes: TransferOperationStore | Portfolio/Account State | store | 策略运行结果和运维可见 | `portfolio/treasury/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/portfolio/treasury/transfer_contracts.py` | classes: LocationType, AssetLocation, InternalAccountDestination, CryptoAddressDestination, BankAccountDestination, AmountMode | Portfolio/Account State | policy, trading model consumer | 策略运行结果和运维可见 | `portfolio/treasury/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/portfolio/treasury/transfer_gateway.py` | classes: TransferSubmission, TransferGateway, SimulatedTransferGateway | Portfolio/Account State | external gateway, trading model consumer | 策略运行结果和运维可见 | `portfolio/treasury/` | Transfer gateway 契约目标应拆：port 到 integrations/ports，simulated implementation 可在 runtime/profile 或 integrations/test harness。 |

## `workspace/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/workspace/__init__.py` | package/export glue | 用户工作区产品 | model/helpers | 内部实现 | `workspace/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/workspace/model.py` | classes: WorkspaceBinding, WorkspaceManifest | 用户工作区产品 | model/helpers | 内部实现 | `workspace/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |
| `kairospy/workspace/repository.py` | classes: WorkspaceRepository, WorkspaceData, Workspace | 用户工作区产品 | repository | 内部实现 | `workspace/` | 边界基本符合目标，仍需用 contract test 固化依赖方向。 |

## former `lifecycle/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/lifecycle/__init__.py` | former package/export glue | 产品族规则 | legacy top-level package | 通过 view/service 间接可见 | `products/common/lifecycle/__init__.py` | 已删除；产品生命周期能力不能作为一级目录回流。 |
| `kairospy/lifecycle/settlement.py` | former classes: AssetFlow, PositionFlow, SettlementResolution, SettlementResolver | 产品族规则 | former settlement lifecycle resolver | 通过 product lifecycle 和 portfolio view 间接可见 | `products/common/lifecycle/settlement.py` | 已删除；通用 settlement resolver 已收口到 products common lifecycle。 |
