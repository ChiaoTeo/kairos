# KairoSpy 文件级定位 Inventory

状态：Draft，source-derived inventory

本文件用于支撑 `quant_system_refactor_plan.md`。它不是按当前文件名直接下结论，而是基于每个 Python 文件的 AST 信号生成第一版定位：top-level class/function、docstring、内部 import、以及是否耦合 backtest/connectors/risk/storage 等系统能力。

阅读方式：

- **实际信号**：来自源码的类、函数、docstring。
- **产品视角**：这个文件最终服务哪个内部产品或用户能力。
- **系统视角**：它在系统里承担模型、服务、状态机、gateway、store、artifact 等哪类职责。
- **用户视角**：普通用户、策略作者、研究员、运维是否直接感知。
- **目标归属**：推荐迁移后的产品文件夹，不等价于立即移动路径。
- **边界备注**：根据源码依赖和符号发现的风险或保留理由。

## 产品视角数量

- External Integrations: 73
- Data Product: 37
- Run Runtime: 31
- Research/Validation: 28
- 分析/模型能力: 18
- Portfolio/Account State: 15
- 交易事实语言: 14
- 产品族规则: 14
- Execution State Machine: 12
- Market Plane: 12
- Risk/Budget: 11
- Reference Data: 8
- 用户入口产品: 8
- Governance/Operations: 7
- Infrastructure: 6
- Strategy SDK: 3
- 用户工作区产品: 3

## `(root)/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/__init__.py` | doc: Kairos quantitative data, workspace, strategy protocol, and run toolkit. | 用户入口产品 | model/helpers | 直接用户入口 | `surface/python_api.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/__main__.py` | funcs: main | 用户入口产品 | integration boundary, persistence, risk-coupled, trading model consumer | 直接用户入口 | `surface/cli/` | CLI dispatch 当前过厚；目标进入 surface/cli，只做命令解析和 use-case dispatch。 |
| `kairospy/cli_output.py` | funcs: resolve_language, render_product_result, render_error, render_status_table, render_key_value_panel, render_command_success | 用户入口产品 | model/helpers | 直接用户入口 | `surface/cli/output.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/cli_progress.py` | classes: TerminalProgressMatrix | 用户入口产品 | model/helpers | 直接用户入口 | `surface/cli/progress.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/configuration.py` | classes: ConfigError, ConfigValue, BinanceCredentials, KairosProjectConfig; funcs: load_project_config_or_none, set_config_value, unset_config_value | Infrastructure | model/helpers | 内部实现 | `infrastructure/configuration.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/product_surface.py` | classes: InputTableRef, DataAddInputError, DataProductNotFoundError, DataLiveDatasetNotConfiguredError, DataDatasetInputError, Data; funcs: data_download, data_apply, data_start, data_add, data_use, data_product_list | 用户入口产品 | model/helpers | 直接用户入口 | `surface/product.py` | 用户产品门面；目标进入 surface/product.py，保持薄 facade。 |
| `kairospy/project.py` | classes: ProjectInitResult; funcs: initialize_project, render_project_init | 用户入口产品 | model/helpers | 直接用户入口 | `surface/cli/project.py or workspace/project.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/provider_surface.py` | funcs: providers_list, provider_doctor, data_product_doctor | 用户入口产品 | model/helpers | 直接用户入口 | `surface/providers.py` | provider 诊断门面；目标进入 surface/providers.py，调用 integrations/data doctor。 |

## `trading/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/trading/__init__.py` | doc: Venue-independent multi-asset trading model. | 交易事实语言 | model/helpers | 通过 view/service 间接可见 | `trading/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/trading/capability.py` | classes: MarketDataKind, OrderType, TimeInForce, MarginMode, PositionMode, ReferenceCapabilities | 交易事实语言 | model/helpers | 通过 view/service 间接可见 | `trading/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/trading/corporate_action.py` | classes: CorporateActionType, SplitEvent, CashDividendEvent, StockDividendEvent, InstrumentExchangeEvent, SymbolChangeEvent | 交易事实语言 | model/helpers | 通过 view/service 间接可见 | `trading/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/trading/derivative_event.py` | classes: DerivativeEventType, DerivativePositionEvent | 交易事实语言 | model/helpers | 通过 view/service 间接可见 | `trading/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/trading/event.py` | classes: UnderlyingPriceUpdated, QuoteUpdated, TradeUpdated, GreeksUpdated, OptionChainDiscovered, BrokerConnected; funcs: envelope | 交易事实语言 | model/helpers | 通过 view/service 间接可见 | `trading/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/trading/execution.py` | classes: TradeSide, TradeExecution, FundingPayment, DividendPayment | 交易事实语言 | model/helpers | 通过 view/service 间接可见 | `trading/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/trading/identity.py` | classes: AssetId, Amount, VenueId, InstrumentId, InstitutionId, AccountType | 交易事实语言 | model/helpers | 通过 view/service 间接可见 | `trading/` | 当前包含 AccountType/AccountKey；目标只保留 AccountRef/identity，不承载余额、权限、锁或凭证。 |
| `kairospy/trading/intent.py` | classes: LegIntent, OpenStructureIntent, CloseStructureIntent, TargetPositionIntent, TargetExposureIntent, CoveredCallIntent | 交易事实语言 | model/helpers | 通过 view/service 间接可见 | `trading/` | 包含 CoveredCall/ProtectivePut 等 archetype intent；目标应迁到 strategy/archetypes 或 intent_builders，只保留通用 EconomicIntent。 |
| `kairospy/trading/ledger.py` | classes: LedgerBook, LedgerEntryType, LedgerEntry, LedgerTransaction, Ledger | 交易事实语言 | model/helpers | 通过 view/service 间接可见 | `trading/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/trading/market_data.py` | classes: OptionChain, Quote, Trade, Bar, OrderBookLevel, OrderBookSnapshot | 交易事实语言 | model/helpers | 通过 view/service 间接可见 | `trading/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/trading/market_state.py` | classes: InstrumentMarketState, MarketState; funcs: apply_market_event | 交易事实语言 | model/helpers | 通过 view/service 间接可见 | `trading/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/trading/order.py` | classes: TriggerPriceSource, SelfTradePrevention, ExecutionInstructions, OrderStatus, Order, LegFill | 交易事实语言 | model/helpers | 通过 view/service 间接可见 | `trading/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/trading/product.py` | classes: ProductType, OptionRight, ExerciseStyle, SettlementType, SettlementSession, ContractType; funcs: is_option_spec, option_multiplier | 交易事实语言 | model/helpers | 通过 view/service 间接可见 | `trading/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/trading/strategy_contract.py` | classes: StrategyLifecycle, StrategySpec, EconomicIntent | 交易事实语言 | model/helpers | 通过 view/service 间接可见 | `trading/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |

## `data/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/data/__init__.py` | package/export glue | Data Product | model/helpers | 数据产品使用/发布 | `data/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/acquisition.py` | classes: ProviderConnector, ProviderRegistry, CoveragePlanner | Data Product | planner | 数据产品使用/发布 | `data/acquisition/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/acquisition_primitives.py` | classes: TimeRange, AcquisitionRequest, AcquisitionEstimate, AcquisitionLimits, AcquisitionPlan | Data Product | model/helpers | 数据产品使用/发布 | `data/acquisition/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/artifact_audit.py` | classes: GovernedArtifactAudit; funcs: audit_governed_artifact | Data Product | artifact | 数据产品使用/发布 | `data/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/bootstrap.py` | funcs: register_default_products, register_configured_products, default_provider_registry, configured_product_specs | Data Product | integration boundary | 数据产品使用/发布 | `data/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/builders/__init__.py` | package/export glue | Data Product | model/helpers | 数据产品使用/发布 | `data/builders/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/builders/ohlcv.py` | classes: EquityOhlcvSourceBinding, EquityOhlcvDataProductBuilder; funcs: equity_hourly_ohlcv_rows, equity_daily_ohlcv_rows, equity_ohlcv_row, equity_symbol, merge_equity_ohlcv_rows, write_equity_ohlcv_dataset | Data Product | persistence | 数据产品使用/发布 | `data/builders/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/builders/planning.py` | classes: TaskRangePlan, UniversePlan, DataProductTaskPlan | Data Product | model/helpers | 数据产品使用/发布 | `data/builders/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/builders/product_builders.py` | classes: ProductSourceBinding, DatasetBuildResult, DataProductBuilder, DataProductBuilderRegistry | Data Product | model/helpers | 数据产品使用/发布 | `data/builders/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/builtin.py` | classes: BuiltInDataProduct, BuiltInDataProductRegistry, BuiltInHistoricalDataProtocol, BuiltInLiveDataProtocol; funcs: default_builtin_protocol_registry | Data Product | model/helpers | 数据产品使用/发布 | `data/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/catalog.py` | classes: DataCatalog | Data Product | model/helpers | 数据产品使用/发布 | `data/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/client.py` | classes: DataUnavailableError, DataQuery, DatasetClient | Data Product | external client, persistence, trading model consumer | 数据产品使用/发布 | `data/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/columnar_publishing.py` | classes: IntradayColumnarRelease; funcs: publish_intraday_staging_parquet | Data Product | persistence | 数据产品使用/发布 | `data/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/contracts.py` | classes: DatasetLayer, DatasetStorageKind, DatasetStatus, QualityLevel, AcquirePolicy, DataView; funcs: data_release_ref, stable_artifact_hash | Data Product | policy, artifact, view | 数据产品使用/发布 | `data/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/curated.py` | classes: ConsolidatedTradeInput, ConsolidatedTradePolicy, ConsolidatedTradeBuilder | Data Product | policy, persistence | 数据产品使用/发布 | `data/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/diagnostics.py` | classes: DataDiagnosticIssue, DataDiagnosticsService, DatasetReadinessService | Data Product | service | 数据产品使用/发布 | `data/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/external_process.py` | classes: ExternalProcessProductBinding, ExternalProcessDataProductBuilder; funcs: publish_external_process_file, command_tuple | Data Product | persistence | 数据产品使用/发布 | `data/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/feed.py` | classes: ReplaySpec, ReplayEventFeed, ReplaySnapshotFeed; funcs: replay_spec | Data Product | backtest-coupled, trading model consumer | 数据产品使用/发布 | `data/` | 依赖 backtest；目标需要切断运行模式泄漏。 |
| `kairospy/data/freshness.py` | classes: LiveViewFreshnessPolicy, LiveViewFreshnessGateResult, LiveViewSubscriptionBinding, LiveViewFreshnessMonitor; funcs: live_view_freshness_policy, live_view_channel_diagnostics, live_view_freshness_evidence, update_live_view_manifest_freshness, live_view_manifest_path, load_live_view_manifest | Data Product | policy, view | 数据产品使用/发布 | `data/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/historical_service.py` | classes: HistoricalDataService | Data Product | service | 数据产品使用/发布 | `data/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/http.py` | funcs: download, download_json | Data Product | model/helpers | 数据产品使用/发布 | `data/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/live_capture.py` | funcs: register_live_capture_release | Data Product | persistence | 数据产品使用/发布 | `data/live/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/live_service.py` | classes: LiveDataService | Data Product | service | 数据产品使用/发布 | `data/live/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/manifest.py` | classes: DataManifestError, DataManifestDataset, DataManifest | Data Product | model/helpers | 数据产品使用/发布 | `data/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/market_snapshot_curation.py` | funcs: curate_complete_market_snapshots | Data Product | persistence, backtest-coupled | 数据产品使用/发布 | `data/publishing/ or market/repository.py` | 依赖 backtest；目标需要切断运行模式泄漏。 |
| `kairospy/data/market_snapshot_storage.py` | classes: MarketSnapshotStorageDriver | Data Product | persistence, backtest-coupled | 数据产品使用/发布 | `data/publishing/ or market/repository.py` | 依赖 backtest；目标需要切断运行模式泄漏。 |
| `kairospy/data/metadata.py` | classes: DataNeedsTimeError, FieldMetadata, DatasetMetadata, DatasetMetadataInference | Data Product | model/helpers | 数据产品使用/发布 | `data/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/preparation.py` | classes: PreparedDataset, DataPromotionPolicyResult, DataPromotionPolicyProfile, DataPreparationService; funcs: evaluate_data_promotion_policy, data_promotion_policy_profile | Data Product | service, policy | 数据产品使用/发布 | `data/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/products.py` | classes: Datasets; funcs: capabilities_payload | Data Product | model/helpers | 数据产品使用/发布 | `data/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/protocols.py` | classes: HistoricalDataRequest, LiveDataRequest, HistoricalDataProtocol, LiveDataProtocol, DataProtocolRegistry | Data Product | model/helpers | 数据产品使用/发布 | `data/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/provider_extensions.py` | classes: ProviderExtensionContext; funcs: provider_extension_specs, register_provider_extensions | Data Product | context | 数据产品使用/发布 | `data/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/publishing.py` | classes: DatasetPublisher; funcs: content_release_id, content_release_id_from_rows, release_path, merge_release_rows, publish_release, register_market_replay_dataset | Data Product | persistence | 数据产品使用/发布 | `data/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/quality.py` | classes: QualityCheck, QualityAssessment, DatasetQualityService | Data Product | service, persistence | 数据产品使用/发布 | `data/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/release_metadata.py` | funcs: ensure_release_metadata, verify_release_metadata | Data Product | persistence | 数据产品使用/发布 | `data/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/snapshot.py` | classes: DataInputSnapshot; funcs: write_data_snapshot | Data Product | persistence | 数据产品使用/发布 | `data/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/source_cache.py` | classes: SourceCacheEntry, SourceCacheStore | Infrastructure | store | 内部实现 | `infrastructure/storage/source_cache.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/surface_features.py` | classes: SurfaceFeaturePublisher; funcs: load_surface_features | 用户入口产品 | persistence | 直接用户入口 | `surface/data_features.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/trade_curation.py` | funcs: curate_sorted_trade_release | Data Product | persistence | 数据产品使用/发布 | `data/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/data/transfer.py` | classes: DatasetCopyResult; funcs: copy_dataset_release | Data Product | model/helpers | 数据产品使用/发布 | `data/publishing/transfer.py or integrations/connectors/transfer/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |

## `market_data/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/market_data/__init__.py` | package/export glue | Market Plane | model/helpers | 内部实现 | `market/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/market_data/capture.py` | classes: CanonicalCaptureManifest, CaptureResourceExceeded, RotatingCanonicalCaptureManifest, CanonicalCaptureWriter, CapturedCanonicalEventSource, RotatingCanonicalCaptureWriter | Market Plane | persistence | 内部实现 | `market/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/market_data/events.py` | classes: MarketEventType, MarketEventEnvelope | Market Plane | trading model consumer | 内部实现 | `market/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/market_data/forward.py` | funcs: zero_rate, cost_of_carry_forward, parity_forward | Market Plane | model/helpers | 内部实现 | `market/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/market_data/projections.py` | classes: CanonicalBarSeriesProjection, QuoteState, CanonicalQuoteProjection, OrderBookGap, OrderBookState, CanonicalOrderBookProjection | Market Plane | trading model consumer | 内部实现 | `market/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/market_data/quality.py` | funcs: validate_option_observation, blocking_issues | Market Plane | model/helpers | 内部实现 | `market/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/market_data/quality_gate.py` | classes: QualitySeverity, EventQualityIssue, EventQualityReport; funcs: validate_events, require_publishable | Market Plane | report, backtest-coupled | 内部实现 | `market/` | 依赖 backtest；目标需要切断运行模式泄漏。 |
| `kairospy/market_data/repository.py` | classes: ParquetMarketEventRepository | Market Plane | repository, persistence, trading model consumer | 内部实现 | `market/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/market_data/soak.py` | classes: MarketDataSoakResult, MarketSoakService, MarketDataRestartCampaignResult; funcs: run_binance_market_soak, run_binance_market_restart_campaign | Market Plane | service, persistence | 内部实现 | `market/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/market_data/stream.py` | classes: EventSource, OverflowPolicy, StreamClosed, StreamOverflow, ConsumerGap, ChannelMetrics | Market Plane | policy | 内部实现 | `market/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/market_data/subscriptions.py` | classes: DeliveryMode, CapturePolicy, MarketDataRequirement, SubscriptionKey, PlannedSubscription, SubscriptionPlan | Market Plane | policy, planner, trading model consumer | 内部实现 | `market/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/market_data/types.py` | classes: DayCount, ForwardMethod, RateNode, RateCurve, DividendInput, ForwardEstimate | Market Plane | trading model consumer | 内部实现 | `market/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |

## `reference/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/reference/__init__.py` | doc: Point-in-time reference data model for assets, products and tradable contracts. | Reference Data | model/helpers | 通过 view/service 间接可见 | `reference/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/reference/access.py` | funcs: definition_at, contract_spec, product_type, trade_cash_asset, settlement_asset | Reference Data | trading model consumer | 通过 view/service 间接可见 | `reference/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/reference/catalog.py` | classes: VersionedRepository, ReferenceCatalog | Reference Data | repository, trading model consumer | 通过 view/service 间接可见 | `reference/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/reference/contracts.py` | classes: AssetType, EntityType, BenchmarkType, AssetDefinition, EntityDefinition, VenueType | Reference Data | trading model consumer | 通过 view/service 间接可见 | `reference/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/reference/factory.py` | funcs: publish_instrument, product_id_for, add_instrument_references | Reference Data | trading model consumer | 通过 view/service 间接可见 | `reference/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/reference/identity.py` | classes: EntityId, BenchmarkId, ProductId, SeriesId, ListingId, ProviderId | Reference Data | trading model consumer | 通过 view/service 间接可见 | `reference/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/reference/repository.py` | classes: ReferenceCatalogRepository; funcs: instrument_to_primitive, instrument_from_primitive | Reference Data | repository, persistence, trading model consumer | 通过 view/service 间接可见 | `reference/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/reference/sync.py` | classes: ReferenceSyncResult, ReferenceSyncService | Reference Data | service | 通过 view/service 间接可见 | `reference/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |

## `products/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/products/__init__.py` | package/export glue | 产品族规则 | model/helpers | 通过 view/service 间接可见 | `products/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/products/calculators.py` | classes: PositionCalculator, SpotCalculator, OptionCalculator, LinearContractCalculator, InverseContractCalculator, QuantoContractCalculator | 产品族规则 | trading model consumer | 通过 view/service 间接可见 | `products/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/products/crypto_option/__init__.py` | package/export glue | 产品族规则 | model/helpers | 通过 view/service 间接可见 | `products/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/products/crypto_option/settlement.py` | classes: CryptoOptionSettlementEvent, CryptoOptionSettlementService, DurableCryptoOptionSettlementService | 产品族规则 | service, trading model consumer | 通过 view/service 间接可见 | `products/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/products/equity/__init__.py` | package/export glue | 产品族规则 | model/helpers | 通过 view/service 间接可见 | `products/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/products/equity/corporate_actions.py` | classes: CorporateActionService | 产品族规则 | service, trading model consumer | 通过 view/service 间接可见 | `products/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/products/future/__init__.py` | package/export glue | 产品族规则 | model/helpers | 通过 view/service 间接可见 | `products/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/products/future/settlement.py` | classes: DerivativeLifecycleService | 产品族规则 | service, trading model consumer | 通过 view/service 间接可见 | `products/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/products/listed_option/__init__.py` | package/export glue | 产品族规则 | model/helpers | 通过 view/service 间接可见 | `products/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/products/listed_option/lifecycle.py` | classes: PhysicalOptionEventType, PhysicalOptionEvent, OptionLifecycleService | 产品族规则 | service, trading model consumer | 通过 view/service 间接可见 | `products/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/products/perpetual/__init__.py` | package/export glue | 产品族规则 | model/helpers | 通过 view/service 间接可见 | `products/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/products/perpetual/funding.py` | classes: FundingEngine | 产品族规则 | engine, trading model consumer | 通过 view/service 间接可见 | `products/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |

## `pricing/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/pricing/__init__.py` | package/export glue | 分析/模型能力 | model/helpers | 通过 view/service 间接可见 | `analytics/pricing/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/pricing/black.py` | funcs: black_scholes, black76, price_with_volatility | 分析/模型能力 | trading model consumer | 通过 view/service 间接可见 | `analytics/pricing/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/pricing/context.py` | classes: PricingContext, PricingContextResolver | 分析/模型能力 | context, trading model consumer | 通过 view/service 间接可见 | `analytics/pricing/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/pricing/implied_vol.py` | funcs: price_bounds, implied_volatility | 分析/模型能力 | trading model consumer | 通过 view/service 间接可见 | `analytics/pricing/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/pricing/option_pricing_contracts.py` | classes: PricingModel, SolverStatus, PricingInput, PricingResult, ImpliedVolResult | 分析/模型能力 | trading model consumer | 通过 view/service 间接可见 | `analytics/pricing/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/pricing/option_valuation.py` | classes: InstrumentValuation, ValuationSnapshot, OptionValuationService | 分析/模型能力 | service, backtest-coupled, trading model consumer | 通过 view/service 间接可见 | `analytics/pricing/` | 当前直接依赖 backtest/capture；目标进入 analytics/valuation，输入只能是 MarketView/ReferenceView/volatility capability。 依赖 backtest；目标需要切断运行模式泄漏。 |

## `volatility/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/volatility/__init__.py` | package/export glue | 分析/模型能力 | model/helpers | 通过 view/service 间接可见 | `analytics/volatility/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/volatility/calibration.py` | funcs: calibrate_svi | 分析/模型能力 | model/helpers | 通过 view/service 间接可见 | `analytics/volatility/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/volatility/contracts.py` | classes: CalibrationStatus, VolObservation, SviParameters, SmileCalibration, ArbitrageDiagnostics, SurfaceSnapshot | 分析/模型能力 | trading model consumer | 通过 view/service 间接可见 | `analytics/volatility/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/volatility/surface.py` | funcs: build_surface, surface_implied_volatility, diagnose_surface | 分析/模型能力 | trading model consumer | 通过 view/service 间接可见 | `analytics/volatility/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/volatility/svi.py` | funcs: total_variance, implied_volatility | 分析/模型能力 | model/helpers | 通过 view/service 间接可见 | `analytics/volatility/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |

## `features/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/features/__init__.py` | package/export glue | 分析/模型能力 | model/helpers | 通过 view/service 间接可见 | `analytics/features/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/features/option_skew.py` | classes: OptionSkewFactorConfig, OptionSkewFactorRuntime, OptionFearCoolingFactorRuntime | 分析/模型能力 | runtime, backtest-coupled, trading model consumer | 通过 view/service 间接可见 | `analytics/features/` | 依赖 backtest；目标需要切断运行模式泄漏。 |
| `kairospy/features/runtime.py` | classes: FactorQuality, FactorSpec, FactorSnapshot, FactorRuntime, FactorRegistry, CanonicalBarFactorRuntime; funcs: implementation_hash, snapshots_hash | 分析/模型能力 | runtime, trading model consumer | 通过 view/service 间接可见 | `analytics/features/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/features/sma.py` | classes: SmaFactorConfig, SmaFactorRuntime; funcs: batch_sma_factors | 分析/模型能力 | runtime, trading model consumer | 通过 view/service 间接可见 | `analytics/features/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/features/us_equity_momentum.py` | classes: UsEquityMomentumPolicy, UsEquityMomentumDatasetBuilder | 分析/模型能力 | policy, persistence, backtest-coupled | 通过 view/service 间接可见 | `analytics/features/` | 依赖 backtest；目标需要切断运行模式泄漏。 |
| `kairospy/features/us_equity_momentum_diagnostics.py` | classes: UsEquityReadinessCheck, UsEquityMomentumDiagnostics | 分析/模型能力 | model/helpers | 通过 view/service 间接可见 | `analytics/features/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/features/volatility.py` | classes: BtcIvRvFeatureBuilder, BtcTermSkewFeatureBuilder, BtcDeribitTradeSkewFeatureBuilder; funcs: build_iv_rv_panel, build_deribit_trade_skew_panel, build_term_skew_panel | 分析/模型能力 | persistence | 通过 view/service 间接可见 | `analytics/features/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |

## `strategy/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/strategy/__init__.py` | package/export glue | Strategy SDK | model/helpers | 策略作者 API | `strategy/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/strategy/protocols.py` | classes: StrategyDecision, StrategyEventKind, StrategyEvent, StrategyContext, Strategy, StrategyProtocol | Strategy SDK | context, decision, trading model consumer | 策略作者 API | `strategy/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/strategy/runtime.py` | classes: GovernedStrategyRuntime | Strategy SDK | runtime, trading model consumer | 策略作者 API | `strategy/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |

## `accounting/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/accounting/__init__.py` | package/export glue | Portfolio/Account State | model/helpers | 策略运行结果和运维可见 | `portfolio/accounting/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/accounting/conversion.py` | classes: ConversionRate, ConversionResult, AssetConversionGraph | Portfolio/Account State | trading model consumer | 策略运行结果和运维可见 | `portfolio/accounting/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/accounting/ledger.py` | classes: LedgerService | Portfolio/Account State | service, trading model consumer | 策略运行结果和运维可见 | `portfolio/accounting/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/accounting/portfolio.py` | classes: AssetBalance, Position, PortfolioSnapshot, Portfolio | Portfolio/Account State | trading model consumer | 策略运行结果和运维可见 | `portfolio/accounting/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |

## `risk/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/risk/__init__.py` | doc: Pre- and post-trade risk controls. | Risk/Budget | model/helpers | 策略运行结果和运维可见 | `risk/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/risk/analytics.py` | classes: PnLExplain, TailRiskResult; funcs: explain_scenario, historical_var_es | Risk/Budget | model/helpers | 策略运行结果和运维可见 | `risk/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/risk/covered_call.py` | funcs: validate_covered_call | Risk/Budget | trading model consumer | 策略运行结果和运维可见 | `risk/` | 策略 archetype 风险校验；目标迁到 strategy/archetypes 或 risk extension，不能留在通用 risk core。 |
| `kairospy/risk/engine.py` | classes: RiskDecisionType, RiskDecision, RiskEngine | Risk/Budget | engine, decision, backtest-coupled, trading model consumer | 策略运行结果和运维可见 | `risk/` | 当前直接依赖 backtest 对象；目标 risk engine 只依赖 PortfolioView/MarketView/ReferenceView。 依赖 backtest；目标需要切断运行模式泄漏。 |
| `kairospy/risk/limits.py` | classes: RiskLimits | Risk/Budget | model/helpers | 策略运行结果和运维可见 | `risk/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/risk/margin.py` | classes: MarginResult, MarginPolicy, SecuritiesCashPolicy, SecuritiesMarginApproximationPolicy, CryptoSpotPolicy, CryptoDerivativesPolicy | Risk/Budget | policy | 策略运行结果和运维可见 | `risk/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/risk/option_structure.py` | funcs: maximum_expiry_loss | Risk/Budget | trading model consumer | 策略运行结果和运维可见 | `risk/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/risk/portfolio_governance.py` | classes: AllocationDecisionType, StrategyAllocation, AllocationDecision, PortfolioAllocator, PositionSizingDecision, PositionSizer | Risk/Budget | decision, trading model consumer | 策略运行结果和运维可见 | `risk/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/risk/scenarios.py` | classes: RevaluationPosition, Scenario, InstrumentScenarioResult, ScenarioResult, ScenarioEngine; funcs: standard_scenario_grid | Risk/Budget | engine, trading model consumer | 策略运行结果和运维可见 | `risk/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/risk/strategy_positions.py` | classes: StrategyPosition, NettedPosition, StrategyPositionBook | Risk/Budget | trading model consumer | 策略运行结果和运维可见 | `risk/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/risk/view.py` | classes: RiskExposure, UnifiedRiskView; funcs: build_risk_view | Risk/Budget | view, risk-coupled, trading model consumer | 策略运行结果和运维可见 | `risk/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |

## `execution/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/execution/__init__.py` | package/export glue | Execution State Machine | model/helpers | 策略运行结果和运维可见 | `execution/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/execution/calibration.py` | classes: ExecutionCalibrationRelease; funcs: load_execution_calibration_release, build_execution_calibration_release | Execution State Machine | integration boundary, persistence, trading model consumer | 策略运行结果和运维可见 | `execution/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/execution/command.py` | classes: OutboxStatus, OrderCommand, OutboxRecord | Execution State Machine | outbox, integration boundary | 策略运行结果和运维可见 | `execution/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/execution/ingestion.py` | classes: ExecutionIngestionService, DurableExecutionIngestionService, DurableAccountingIngestionService | Execution State Machine | service, integration boundary, trading model consumer | 策略运行结果和运维可见 | `execution/` | 依赖 connector；目标需要改为 port/capability。 |
| `kairospy/execution/intent_status.py` | classes: IntentStatus, IntentScope, IntentExecutionView, IntentExecutionTracker; funcs: intent_scope | Execution State Machine | view, trading model consumer | 策略运行结果和运维可见 | `execution/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/execution/order_state.py` | classes: DurableOrderStatus, DurableOrderRecord; funcs: require_order_transition | Execution State Machine | integration boundary | 策略运行结果和运维可见 | `execution/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/execution/outbox.py` | classes: DurableOrderCommandService, DurableOrderDispatcher | Execution State Machine | service, integration boundary | 策略运行结果和运维可见 | `execution/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/execution/planner.py` | classes: LeggingPolicy, NativeComboPlan, SequentialLegPlan; funcs: plan_combo | Execution State Machine | policy, integration boundary, trading model consumer | 策略运行结果和运维可见 | `execution/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/execution/policy.py` | classes: ExecutionMode, PartialFillPolicy, ExecutionPolicy | Execution State Machine | policy, trading model consumer | 策略运行结果和运维可见 | `execution/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/execution/recovery.py` | classes: OrderRecoveryReport, VenueOrderRecoveryService | Execution State Machine | service, report, integration boundary, trading model consumer | 策略运行结果和运维可见 | `execution/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/execution/router.py` | classes: ExecutionRiskLimits, ExecutionRouter | Execution State Machine | router, integration boundary | 策略运行结果和运维可见 | `execution/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/execution/strategy_planner.py` | classes: StrategyExecutionPlan, EconomicExecutionPlan; funcs: plan_economic_intent, plan_strategy_intent | Execution State Machine | integration boundary, trading model consumer | 策略运行结果和运维可见 | `execution/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |

## `application/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/application/__init__.py` | doc: Application-layer composition, configuration, and runtime ports. | Run Runtime | backtest-coupled | 运行配置/运维间接可见 | `runtime/` migration package glue | 当前只是 application 组合层导出；目标拆到 runtime/surface/governance 后只保留兼容导出。 |
| `kairospy/application/async_runtime.py` | classes: AsyncKairosRuntime | Run Runtime | runtime | 运行配置/运维间接可见 | `runtime/supervisor.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/application/attribution.py` | classes: SignalAttribution, PortfolioAttribution, ExecutionAttribution, RunAttribution; funcs: build_run_attribution | Governance/Operations | model/helpers | 内部实现 | `governance/artifact.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/application/clock.py` | classes: Clock, SystemClock, FixedClock | Run Runtime | model/helpers | 运行配置/运维间接可见 | `runtime/clock.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/application/config.py` | classes: RuntimePaths, ApplicationConfig | Run Runtime | runtime, integration boundary | 运行配置/运维间接可见 | `runtime/config.py or infrastructure/configuration.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/application/immediate_backtest.py` | classes: ImmediateBacktestPortfolio, ImmediateBacktestTrade, ImmediateIntentBacktestResult; funcs: run_immediate_target_backtest | Run Runtime | risk-coupled, trading model consumer | 回测用户可见结果 | `runtime/profiles/backtest/compat_immediate.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/application/modes.py` | classes: RunModeComposition, RuntimeFeedServicePlan, RuntimeFeedPlan, RuntimeFeedServiceBundle, RuntimeExecutionServicePlan, RuntimeExecutionPlan; funcs: backtest_composition, historical_simulation_composition, paper_trading_composition, live_composition, runtime_feed_plan, runtime_execution_plan | Run Runtime | service, runtime | 运行配置/运维间接可见 | `runtime/contracts.py + runtime/profiles/` | 当前表达 RunModeComposition；目标应迁入 runtime/contracts + runtime/profiles。 |
| `kairospy/application/recovery.py` | classes: RuntimeRecoveryResult, RuntimeRecovery, RuntimeRecoveryService | Run Runtime | service, runtime, integration boundary, persistence | 运行配置/运维间接可见 | `runtime/recovery.py + governance/reconciliation.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/application/run_artifact.py` | classes: RunArtifact, RunArtifactRepository | Governance/Operations | repository, artifact, persistence | 内部实现 | `governance/artifact.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/application/runtime.py` | classes: RuntimeStatus, ProbeResult, ReadinessProbe, FunctionProbe, PersistenceProbe, KairosApplication | Run Runtime | runtime, readiness probe, trading model consumer | 运行配置/运维间接可见 | `runtime/application.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/application/runtime_failure_policy.py` | funcs: run_runtime_failure_policy | Governance/Operations | external gateway, integration boundary, trading model consumer | 内部实现 | `governance/incidents.py + runtime/recovery.py` | 依赖 connector；目标需要改为 port/capability。 |
| `kairospy/application/runtime_reference_artifact.py` | classes: RuntimeReferenceArtifactResult; funcs: run_runtime_reference_artifact | Run Runtime | runtime, artifact, integration boundary, persistence | 运行配置/运维间接可见 | `runtime/profiles/live/reference_artifact.py` | 依赖 connector；目标需要改为 port/capability。 |
| `kairospy/application/service_supervisor.py` | classes: ServiceCriticality, ManagedServiceStatus, ManagedServiceSpec, ServiceFault, ManagedServiceSnapshot, AsyncServiceSupervisor | Run Runtime | service, supervisor | 运行配置/运维间接可见 | `runtime/supervisor.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/application/strategy_run_loop.py` | classes: StrategyRunResult, StrategyRunHooks, CanonicalBarMarketProjection, GovernedStrategyRunLoop | Run Runtime | persistence, backtest-coupled, trading model consumer | 运行配置/运维间接可见 | `runtime/kernel.py` | 当前是策略循环组合点；目标应拆为 runtime/kernel + strategy Context assembly。 依赖 backtest；目标需要切断运行模式泄漏。 |
| `kairospy/application/supervisor.py` | classes: RuntimeBackgroundService, RecoveryBackgroundService, SupervisorCycle, RuntimeSupervisor; funcs: write_soak_artifact | Run Runtime | service, runtime, supervisor, persistence | 运行配置/运维间接可见 | `runtime/supervisor.py + governance/observability.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |

## `backtest/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/backtest/__init__.py` | doc: Deterministic option backtesting. | Run Runtime | model/helpers | 回测用户可见结果 | `runtime/profiles/backtest/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/backtest/calendar.py` | classes: TradingSession, TradingCalendar, AlwaysOpenCalendar, CalendarRegistry; funcs: us_market_holidays, us_market_early_closes | Run Runtime | trading model consumer | 回测用户可见结果 | `runtime/profiles/backtest/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/backtest/clock.py` | classes: BacktestClock | Run Runtime | model/helpers | 回测用户可见结果 | `runtime/profiles/backtest/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/backtest/engine.py` | classes: DeterministicIds, BacktestEngine | Run Runtime | engine, persistence, risk-coupled, trading model consumer | 回测用户可见结果 | `runtime/profiles/backtest/` | 当前是耦合中心；目标作为 BacktestProfile 历史实现/adapter 输入，而不是 live runtime kernel。 |
| `kairospy/backtest/execution.py` | classes: ComboQuote, ExecutionPlanner; funcs: combo_quote | Run Runtime | planner, trading model consumer | 回测用户可见结果 | `runtime/profiles/backtest/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/backtest/feed.py` | classes: SettlementType, InstrumentLifecycleSnapshot, MarketSnapshot, DatasetManifest, MarketReplayDataset, MarketSnapshotReplayFeed; funcs: build_manifest | Run Runtime | persistence, trading model consumer | 回测用户可见结果 | `runtime/profiles/backtest/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/backtest/fill.py` | classes: FillModelType, CommissionModel, FixedCommissionModel, FillAttempt, ListedOptionComboFillModel, SingleAssetOrder | Run Runtime | trading model consumer | 回测用户可见结果 | `runtime/profiles/backtest/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/backtest/maker.py` | classes: BookEventType, IncrementalBookEvent, MakerOrderState, MakerEventResult, FifoMakerFillModel, HybridAction | Run Runtime | decision, trading model consumer | 回测用户可见结果 | `runtime/profiles/backtest/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/backtest/metrics.py` | funcs: calculate_metrics | Run Runtime | backtest-coupled, trading model consumer | 回测用户可见结果 | `runtime/profiles/backtest/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/backtest/portfolio.py` | classes: Position, StructurePosition, PositionSnapshot, PortfolioSnapshot, BacktestPortfolio | Run Runtime | risk-coupled, trading model consumer | 回测用户可见结果 | `runtime/profiles/backtest/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/backtest/repository.py` | classes: BacktestRepository | Run Runtime | repository, persistence | 回测用户可见结果 | `runtime/profiles/backtest/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/backtest/result.py` | classes: ResultStatus, BacktestConfig, EquityPoint, BacktestResult | Run Runtime | backtest-coupled, risk-coupled, trading model consumer | 回测用户可见结果 | `runtime/profiles/backtest/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/backtest/settlement.py` | funcs: intrinsic_value, due_settlements | Run Runtime | trading model consumer | 回测用户可见结果 | `runtime/profiles/backtest/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/backtest/synthetic_scenarios.py` | classes: SyntheticScenario, DatasetReadiness; funcs: build_synthetic_backtest_dataset, assess_dataset | Run Runtime | trading model consumer | 回测用户可见结果 | `runtime/profiles/backtest/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |

## `capture/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/capture/__init__.py` | doc: Capture, snapshot, and option-series helpers. | Research/Validation | model/helpers | 研究员工作流 | `research/capture/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/capture/data_store.py` | classes: CollectionSession, CollectionManifest, MarketSnapshotCollectionPublisher; funcs: merge_datasets | Research/Validation | persistence, backtest-coupled | 研究员工作流 | `research/capture/` | 依赖 backtest；目标需要切断运行模式泄漏。 |
| `kairospy/capture/features.py` | classes: FeatureSnapshot, FeatureEngine; funcs: build_features | Research/Validation | engine | 研究员工作流 | `research/capture/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/capture/normalized_series.py` | classes: NormalizedQuoteProvider, NormalizedSeriesCaptureService | Research/Validation | service, persistence, backtest-coupled, trading model consumer | 研究员工作流 | `research/capture/` | 依赖 backtest；目标需要切断运行模式泄漏。 |
| `kairospy/capture/option_capture.py` | classes: OptionCaptureService | Research/Validation | service, integration boundary, persistence, trading model consumer | 研究员工作流 | `research/capture/` | 依赖 connector；目标需要改为 port/capability。 |
| `kairospy/capture/option_snapshot_analysis.py` | classes: OptionSnapshotMetricRow, OptionSnapshotAnalysis, IvSmilePoint, PutCallPair; funcs: analyze_option_snapshot | Research/Validation | trading model consumer | 研究员工作流 | `research/capture/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/capture/option_universe_selector.py` | funcs: select_expirations, select_strikes, select_instruments | Research/Validation | trading model consumer | 研究员工作流 | `research/capture/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/capture/report.py` | funcs: write_csv, summarize | Research/Validation | model/helpers | 研究员工作流 | `research/capture/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/capture/retention.py` | classes: RetainedLeg, RetentionManifest, DeltaLegWatchlist | Research/Validation | persistence, backtest-coupled, trading model consumer | 研究员工作流 | `research/capture/` | 依赖 backtest；目标需要切断运行模式泄漏。 |
| `kairospy/capture/series.py` | classes: SeriesCaptureSpec, SeriesCaptureProgress, SeriesCaptureService | Research/Validation | service, integration boundary, persistence, backtest-coupled | 研究员工作流 | `research/capture/` | 依赖 backtest；目标需要切断运行模式泄漏。 依赖 connector；目标需要改为 port/capability。 |
| `kairospy/capture/snapshot.py` | classes: DataQualityIssue, InstrumentSnapshot, OptionCaptureSnapshot, ReferenceSnapshotEvidence; funcs: build_reference_evidence, build_snapshot | Research/Validation | trading model consumer | 研究员工作流 | `research/capture/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/capture/spec.py` | classes: MarketDataType, OptionChainCaptureSpec | Research/Validation | trading model consumer | 研究员工作流 | `research/capture/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/capture/tutorial_data.py` | funcs: tutorial_sma_bars, ensure_sma_tutorial_dataset | Research/Validation | persistence, trading model consumer | 研究员工作流 | `research/capture/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |

## `validation/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/validation/__init__.py` | doc: Governed validation contracts and gates. | Research/Validation | model/helpers | 研究员工作流 | `research/validation/ + governance/promotion.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/validation/artifacts.py` | classes: ValidationArtifactWriter | Research/Validation | artifact | 研究员工作流 | `research/validation/ + governance/promotion.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/validation/audit.py` | classes: GovernanceAudit; funcs: audit_governance | Research/Validation | model/helpers | 研究员工作流 | `research/validation/ + governance/promotion.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/validation/bootstrap.py` | funcs: block_bootstrap_mean_ci, newey_west_mean_t | Research/Validation | model/helpers | 研究员工作流 | `research/validation/ + governance/promotion.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/validation/claims.py` | classes: ClaimDecision; funcs: authorize_claim | Research/Validation | decision | 研究员工作流 | `research/validation/ + governance/promotion.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/validation/contracts.py` | classes: ValidationLevel, EvidenceStatus, ProductProtocol, ReturnDriver, ExecutionArchetype, OutOfSampleEvidence | Research/Validation | model/helpers | 研究员工作流 | `research/validation/ + governance/promotion.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/validation/data_gaps.py` | funcs: build_data_gap_plan | Research/Validation | model/helpers | 研究员工作流 | `research/validation/ + governance/promotion.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/validation/gates.py` | classes: GateRequirement, GateDecision, ValidationGate | Research/Validation | decision | 研究员工作流 | `research/validation/ + governance/promotion.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/validation/predictability.py` | classes: PredictabilityResult; funcs: validate_predictability | Research/Validation | model/helpers | 研究员工作流 | `research/validation/ + governance/promotion.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/validation/protocols.py` | classes: ProtocolDecision; funcs: validate_product_protocol, validate_return_driver_protocol | Research/Validation | decision | 研究员工作流 | `research/validation/ + governance/promotion.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/validation/report.py` | funcs: render_validation_report | Research/Validation | model/helpers | 研究员工作流 | `research/validation/ + governance/promotion.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/validation/robustness.py` | classes: RobustnessResult; funcs: assess_robustness | Research/Validation | model/helpers | 研究员工作流 | `research/validation/ + governance/promotion.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/validation/samples.py` | funcs: overlap_adjusted_effective_samples, approximate_required_samples, assess_sample_sufficiency | Research/Validation | model/helpers | 研究员工作流 | `research/validation/ + governance/promotion.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/validation/split.py` | classes: TimeSplit; funcs: chronological_split, walk_forward_splits | Research/Validation | model/helpers | 研究员工作流 | `research/validation/ + governance/promotion.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/validation/test_windows.py` | classes: TestWindowUse, TestWindowRegistry | Research/Validation | model/helpers | 研究员工作流 | `research/validation/ + governance/promotion.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |

## `orchestration/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/orchestration/__init__.py` | package/export glue | Run Runtime | model/helpers | 运行配置/运维间接可见 | `runtime/` + `governance/` migration package glue | 当前只是 orchestration 组合层导出；目标拆到 runtime/store、execution/recovery、governance 后只保留兼容导出。 |
| `kairospy/orchestration/coordinator.py` | classes: PersistedOrderRecord, PersistedComboOrderRecord, PersistedCancellationRecord, ExecutionCoordinator | Run Runtime | integration boundary, persistence, trading model consumer | 运行配置/运维间接可见 | `runtime/coordinator.py + execution/recovery.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/orchestration/event_log.py` | classes: PersistentEventLog | Run Runtime | persistence | 运行配置/运维间接可见 | `runtime/store/event_log.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/orchestration/faults.py` | classes: RuntimeFaultPoint, RuntimeFaultInjector, InjectedRuntimeFailure, OneShotRuntimeFaultInjector; funcs: inject | Run Runtime | runtime | 运行配置/运维间接可见 | `runtime/testing/faults.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/orchestration/kill_switch.py` | classes: KillSwitchResult, KillSwitch | Governance/Operations | integration boundary, trading model consumer | 内部实现 | `governance/kill_switch.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/orchestration/monitoring.py` | classes: AlertSeverity, OperationalAlert, OperationalMonitor | Governance/Operations | model/helpers | 内部实现 | `governance/observability.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/orchestration/reconciliation.py` | classes: ReconciliationDifference, ReconciliationReport, ReconciliationService | Governance/Operations | service, report, integration boundary, risk-coupled | 内部实现 | `governance/reconciliation.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/orchestration/runtime_store.py` | classes: ManualOrderResolution, DurableExecutionRecord, SQLiteRuntimeStore | Run Runtime | store, runtime, integration boundary, persistence | 运行配置/运维间接可见 | `runtime/store/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/orchestration/strategy_monitoring.py` | classes: StrategyHealth, StrategyMonitoringLimits, StrategyMonitoringSnapshot, StrategyHealthDecision, StrategyHealthMonitor | Governance/Operations | decision | 内部实现 | `governance/strategy_monitoring.py` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |

## `connectors/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/connectors/__init__.py` | doc: External system connectors for market data, reference data, execution, and transfers. | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/artifacts.py` | classes: ProviderEstimate, SourceArtifact, ProviderEvent, ProviderHealth | External Integrations | artifact | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/binance/__init__.py` | package/export glue | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/binance/account_gateway.py` | classes: BinanceAccountGateway, BinanceOptionsAccountGateway | External Integrations | external gateway, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/binance/datasets.py` | classes: BinanceSpotDatasetConnector, BinanceUsdmPerpetualHourlyDatasetConnector, BinanceOptionQuotesDatasetConnector | External Integrations | persistence | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/binance/execution_gateway.py` | classes: BinanceExecutionGateway, BinanceOptionsExecutionGateway | External Integrations | external gateway, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/binance/funding_ingestion.py` | classes: FundingBackfillReport, BinanceDurableFundingBackfill | External Integrations | report, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/binance/funding_settlement.py` | classes: BinanceFundingSettlementClient | External Integrations | external client, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/binance/historical_archive.py` | classes: BinanceSpotArchiveProvider, BinanceUsdmPerpetualHourlyArchiveProvider, GracefulShutdown | External Integrations | persistence | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/binance/market_data_client.py` | classes: BinanceMarketDataClient; doc: Binance REST market data snapshot client. | External Integrations | external client, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/binance/market_stream.py` | classes: WebSocketConnection, WebSocketConnector, WebSocketClientConnection, WebSocketClientConnector, BinanceStreamSession; funcs: websocket_url, parse_market_stream_event; doc: Binance public market stream utilities and reconnecting stream sessions. | External Integrations | external client, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/binance/option_market_snapshot.py` | classes: OptionMarketSnapshot; funcs: parse_option_market_snapshot | External Integrations | trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/binance/options_archive.py` | classes: BinanceOptionsEohArchiveProvider; funcs: normalize_eoh_rows | External Integrations | persistence | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/binance/order_book.py` | classes: OrderBookSnapshotProvider, BinanceOrderBookSyncFault, BinanceOrderBookSnapshotProvider, BinanceOrderBookSyncMetrics, BinanceOrderBookSyncService | External Integrations | service, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/binance/order_recovery.py` | classes: RecoverySnapshot, BinanceRecoveryService | External Integrations | service, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/binance/reference_data.py` | classes: BinanceSpotReferenceDataClient, BinanceFuturesReferenceDataClient, BinanceOptionsReferenceDataClient; doc: Binance reference data clients and product definition builders. | External Integrations | external client, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/binance/request_signing.py` | classes: BinanceSigner; funcs: synchronize_clock; doc: Binance request signing and clock synchronization. | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/binance/rest_transport.py` | classes: BinanceTransport, UrllibBinanceTransport, RateLimiter; doc: Binance REST transport protocol, urllib implementation, and rate limiter. | External Integrations | transport | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/binance/runtime_feed.py` | classes: BinanceRuntimeFeed, BinanceRuntimeFeedFactory | External Integrations | runtime, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/binance/stream.py` | classes: BinanceCanonicalStreamService | External Integrations | service, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/binance/user_data_stream.py` | classes: UserFillUpdate, BalanceUpdate, BinanceUserDataStreamService, BinanceUserStreamProcessor; funcs: parse_user_stream_event | External Integrations | service, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/codecs.py` | classes: ProviderCodec | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/data_planes.py` | classes: DataPlaneEndpoint, ProviderDataPlaneSpec, ProviderDataPlane | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/deribit/__init__.py` | package/export glue | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/deribit/datasets.py` | classes: DeribitDvolDatasetConnector, DeribitOptionTradesDatasetConnector, DeribitOptionSnapshotDatasetConnector | External Integrations | persistence | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/deribit/historical.py` | classes: DeribitDvolProvider | External Integrations | persistence | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/deribit/option_chain.py` | classes: DeribitOptionChainProvider; funcs: normalize_chain | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/deribit/trade_history.py` | classes: DeribitOptionTradeHistoryProvider; funcs: normalize_deribit_trades | External Integrations | persistence | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/execution.py` | classes: ExecutionService, ComboExecutionService, ExecutionServiceSpec | External Integrations | service, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/ibkr/__init__.py` | package/export glue | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/ibkr/account_gateway.py` | classes: IbkrAccountGateway | External Integrations | external gateway, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/ibkr/execution_gateway.py` | classes: IbkrExecutionGateway; funcs: normalize_ibkr_execution | External Integrations | external gateway, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/ibkr/ingestion.py` | classes: IbkrDurableFillIngestion | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/ibkr/market_data_client.py` | classes: IbkrMarketDataClient | External Integrations | external client, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/ibkr/option_chain_provider.py` | classes: SpxwOptionChainProvider, IbkrSpxwOptionChainProvider; funcs: decimal_or_none | External Integrations | trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/ibkr/reference_data.py` | classes: IbkrReferenceDataClient | External Integrations | external client, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/ibkr/session.py` | classes: IbkrSession | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/market_data_router.py` | classes: CompositeMarketDataClient | External Integrations | external client, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/massive/__init__.py` | package/export glue | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/massive/client.py` | classes: MassiveError, MassiveResponse, MassiveTransport, UrllibMassiveTransport, MassiveClient; funcs: redact_url | External Integrations | external client, transport | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/massive/close_implied_volatility.py` | classes: OptionCloseImpliedVolatilityPipeline | External Integrations | persistence, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/massive/config.py` | classes: MassiveConfig | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/massive/corporate_actions.py` | classes: MassiveCorporateActionDecoder | External Integrations | trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/massive/curated.py` | classes: MassiveMarketSnapshotBuilder | External Integrations | persistence, backtest-coupled, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 依赖 backtest；目标需要切断运行模式泄漏。 |
| `kairospy/connectors/massive/daily_ohlcv.py` | classes: OpraInventoryEntry, OptionDailyOhlcvPipeline, SpxwDailyOhlcvPipeline | External Integrations | persistence, backtest-coupled | provider 接入/诊断间接可见 | `integrations/connectors/` | 依赖 backtest；目标需要切断运行模式泄漏。 |
| `kairospy/connectors/massive/datasets.py` | classes: MassiveOptionProductConfig, MassiveEquityDailyOhlcvProductConfig, MassiveEquityDailyOhlcvDatasetConnector, MassiveEquityDailyMarketOhlcvDatasetConnector, MassiveEquityHourlyOhlcvDatasetConnector, MassiveOptionEventsDatasetConnector | External Integrations | persistence, backtest-coupled | provider 接入/诊断间接可见 | `integrations/connectors/` | 依赖 backtest；目标需要切断运行模式泄漏。 |
| `kairospy/connectors/massive/decoder.py` | funcs: decode_quotes, decode_trades, decode_option_snapshots, decode_bars | External Integrations | trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/massive/entitlement_diagnostics.py` | classes: MassiveEntitlementReport, MassiveEntitlementDiagnostics | External Integrations | report | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/massive/equity_daily_ohlcv.py` | classes: MassiveEquityDailyOhlcvPipeline, MassiveEquityHourlyOhlcvPipeline | External Integrations | persistence, backtest-coupled | provider 接入/诊断间接可见 | `integrations/connectors/` | 依赖 backtest；目标需要切断运行模式泄漏。 |
| `kairospy/connectors/massive/equity_identity.py` | classes: MassiveEquityIdentityResult, MassiveEquityIdentityResolver | External Integrations | persistence, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/massive/market_data.py` | classes: MassiveAggregateBarsRequest, MassiveAggregateBarsArtifact, MassiveAggregateBarsResource, MassiveHistoricalMarketDataService | External Integrations | service, artifact, integration boundary | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/massive/pipeline.py` | classes: MassiveOptionDataPipeline | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/massive/reference.py` | classes: MassiveReferenceImporter | External Integrations | backtest-coupled, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 依赖 backtest；目标需要切断运行模式泄漏。 |
| `kairospy/connectors/massive/reference_pipeline.py` | classes: MassiveReferencePipeline | External Integrations | persistence | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/massive/reference_store.py` | classes: MassiveReferenceStore | External Integrations | store, persistence | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/massive/vendor_archive.py` | classes: OutsideDownloadWindow, ArchivedRequest, MassiveVendorArchiveClient, MassiveFlatFileClient, MassiveFlatFileBatchDownloader; funcs: request_fingerprint | External Integrations | external client, persistence, backtest-coupled | provider 接入/诊断间接可见 | `integrations/connectors/` | 依赖 backtest；目标需要切断运行模式泄漏。 |
| `kairospy/connectors/massive/websocket.py` | classes: MassiveWebSocketClient, MassiveLiveStream, MassiveStreamFault, MassiveCanonicalStreamService | External Integrations | external client, service | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/provider_contracts.py` | classes: ProviderConnector | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/resources.py` | classes: ProviderResource, ProviderResourceSpec | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/services.py` | classes: ProviderService, HistoricalMarketDataService, ProviderServiceSpec | External Integrations | service | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/simulated.py` | classes: SimulatedExecutionAccountGateway | External Integrations | external gateway, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/transfer/__init__.py` | package/export glue | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/transfer/bank.py` | classes: BankTransferProviderClient, BankTransferGateway | External Integrations | external gateway, external client, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/transfer/binance.py` | classes: BinanceWalletRoute, BinanceTransferGateway | External Integrations | external gateway, integration boundary, trading model consumer | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/connectors/transports.py` | classes: TransportRequest, TransportResponse, ProviderTransport | External Integrations | transport | provider 接入/诊断间接可见 | `integrations/connectors/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |

## `ports/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/ports/__init__.py` | package/export glue | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/ports/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/ports/account.py` | package/export glue | External Integrations | integration boundary | provider 接入/诊断间接可见 | `integrations/ports/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/ports/execution.py` | package/export glue | External Integrations | integration boundary | provider 接入/诊断间接可见 | `integrations/ports/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/ports/market_data.py` | package/export glue | External Integrations | integration boundary | provider 接入/诊断间接可见 | `integrations/ports/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/ports/reference_data.py` | package/export glue | External Integrations | integration boundary | provider 接入/诊断间接可见 | `integrations/ports/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/ports/venue.py` | classes: Environment, VenueOrderStatus, OrderRequest, OrderAck, VenueBalance, ComboLegRequest | External Integrations | trading model consumer | provider 接入/诊断间接可见 | `integrations/ports/` | 当前把 account/execution/reference 多种 port 契约混合；目标拆到 integrations/ports/*。 |

## `contracts/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/contracts/__init__.py` | doc: Versioned, transport-independent contracts shared by runtime components. | External Integrations | model/helpers | provider 接入/诊断间接可见 | `integrations/contracts/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/contracts/market_data.py` | classes: MarketEventKind, QuotePayload, TradePayload, BarPayload, OrderBookLevelPayload, OrderBookDeltaPayload; funcs: canonicalize_market_event, canonical_from_trading_market_data | External Integrations | trading model consumer | provider 接入/诊断间接可见 | `integrations/contracts/` | 外部 canonical market contract；目标可能是 integrations/contracts 或 market/events，取决于是否是 provider-neutral envelope 还是运行时事件。 |

## `storage/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/storage/__init__.py` | doc: File-backed capture and artifact persistence. | Infrastructure | model/helpers | 内部实现 | `infrastructure/storage/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/storage/codec.py` | funcs: to_primitive, from_primitive, snapshot_from_primitive, snapshot_to_primitive, restore_primitives, event_to_primitive | Infrastructure | trading model consumer | 内部实现 | `infrastructure/storage/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/storage/data_lake.py` | funcs: sha256_bytes, write_json, utc_midnight, write_daily_dataset, write_intraday_dataset, write_event_dataset | Infrastructure | model/helpers | 内部实现 | `infrastructure/storage/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/storage/repository.py` | classes: RunStatus, RunManifest, FileOptionCaptureRepository; funcs: new_manifest | Infrastructure | repository, trading model consumer | 内部实现 | `infrastructure/storage/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |

## `treasury/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/treasury/__init__.py` | doc: Asset movement planning, execution state and ledger coordination. | Portfolio/Account State | model/helpers | 策略运行结果和运维可见 | `portfolio/treasury/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/treasury/accounting.py` | classes: TreasuryAccountingProjector | Portfolio/Account State | trading model consumer | 策略运行结果和运维可见 | `portfolio/treasury/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/treasury/coordinator.py` | classes: TreasuryCoordinator | Portfolio/Account State | model/helpers | 策略运行结果和运维可见 | `portfolio/treasury/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/treasury/ledger_posting.py` | classes: TreasuryLedgerPostingService | Portfolio/Account State | service, trading model consumer | 策略运行结果和运维可见 | `portfolio/treasury/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/treasury/planner.py` | classes: TreasuryPlanner | Portfolio/Account State | planner | 策略运行结果和运维可见 | `portfolio/treasury/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/treasury/policy.py` | classes: TransferPolicy | Portfolio/Account State | policy, trading model consumer | 策略运行结果和运维可见 | `portfolio/treasury/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/treasury/reconciliation.py` | classes: TransferObservation, TransferReconciliationService | Portfolio/Account State | service, trading model consumer | 策略运行结果和运维可见 | `portfolio/treasury/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/treasury/repository.py` | classes: SQLiteTreasuryRepository | Portfolio/Account State | repository, trading model consumer | 策略运行结果和运维可见 | `portfolio/treasury/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/treasury/state_machine.py` | classes: TransferOperationStore | Portfolio/Account State | store | 策略运行结果和运维可见 | `portfolio/treasury/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/treasury/transfer_contracts.py` | classes: LocationType, AssetLocation, InternalAccountDestination, CryptoAddressDestination, BankAccountDestination, AmountMode | Portfolio/Account State | policy, trading model consumer | 策略运行结果和运维可见 | `portfolio/treasury/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/treasury/transfer_gateway.py` | classes: TransferSubmission, TransferGateway, SimulatedTransferGateway | Portfolio/Account State | external gateway, trading model consumer | 策略运行结果和运维可见 | `portfolio/treasury/` | Transfer gateway 契约目标应拆：port 到 integrations/ports，simulated implementation 可在 runtime/profile 或 integrations/test harness。 |

## `workspace/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/workspace/__init__.py` | package/export glue | 用户工作区产品 | model/helpers | 内部实现 | `workspace/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/workspace/model.py` | classes: WorkspaceBinding, WorkspaceManifest | 用户工作区产品 | model/helpers | 内部实现 | `workspace/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/workspace/repository.py` | classes: WorkspaceRepository, WorkspaceData, Workspace | 用户工作区产品 | repository | 内部实现 | `workspace/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |

## `lifecycle/`

| 文件 | 实际信号 | 产品视角 | 系统视角 | 用户视角 | 目标归属 | 边界备注 |
|---|---|---|---|---|---|---|
| `kairospy/lifecycle/__init__.py` | package/export glue | 产品族规则 | model/helpers | 通过 view/service 间接可见 | `products/common/lifecycle/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
| `kairospy/lifecycle/settlement.py` | classes: AssetFlow, PositionFlow, SettlementResolution, SettlementResolver | 产品族规则 | trading model consumer | 通过 view/service 间接可见 | `products/common/lifecycle/` | 边界基本符合目标，但迁移时仍需用 contract test 固化依赖方向。 |
