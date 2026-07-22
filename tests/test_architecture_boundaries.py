from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
TRADING = ROOT / "kairospy" / "trading"
IDENTITY = ROOT / "kairospy" / "identity"
MARKET = ROOT / "kairospy" / "market"
MARKET_DATA = ROOT / "kairospy" / "market_data"
APPLICATION = ROOT / "kairospy" / "application"
RUNTIME = ROOT / "kairospy" / "runtime"
GOVERNANCE = ROOT / "kairospy" / "governance"
INFRASTRUCTURE = ROOT / "kairospy" / "infrastructure"
SURFACE = ROOT / "kairospy" / "surface"
ORCHESTRATION = ROOT / "kairospy" / "orchestration"
BACKTEST = ROOT / "kairospy" / "backtest"
RESEARCH = ROOT / "kairospy" / "research"
PORTFOLIO = ROOT / "kairospy" / "portfolio"
PRODUCTS = ROOT / "kairospy" / "products"
REFERENCE = ROOT / "kairospy" / "reference"
RISK = ROOT / "kairospy" / "risk"
INTEGRATIONS = ROOT / "kairospy" / "integrations"
CONNECTORS = ROOT / "kairospy" / "connectors"
PORTS = ROOT / "kairospy" / "ports"
CONTRACTS = ROOT / "kairospy" / "contracts"
ANALYTICS = ROOT / "kairospy" / "analytics"
FEATURES = ROOT / "kairospy" / "features"
PRICING = ROOT / "kairospy" / "pricing"
VOLATILITY = ROOT / "kairospy" / "volatility"
ACCOUNTING = ROOT / "kairospy" / "accounting"
TREASURY = ROOT / "kairospy" / "treasury"
LIFECYCLE = ROOT / "kairospy" / "lifecycle"
STORAGE = ROOT / "kairospy" / "storage"
CAPTURE = ROOT / "kairospy" / "capture"
VALIDATION = ROOT / "kairospy" / "validation"
PORTFOLIO_ACCOUNTING = PORTFOLIO / "accounting"
PORTFOLIO_TREASURY = PORTFOLIO / "treasury"
BACKTEST_PROFILE = RUNTIME / "profiles" / "backtest"
SIMULATION_PROFILE = RUNTIME / "profiles" / "simulation"
RUNTIME_STORE = RUNTIME / "store"
RUNTIME_TESTING = RUNTIME / "testing"


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_identity_owner_package_does_not_depend_on_upper_layers(self) -> None:
        forbidden = {
            "kairospy.portfolio.accounting",
            "kairospy.application",
            "kairospy.backtest",
            "kairospy.research.capture",
            "kairospy.integrations.connectors",
            "kairospy.integrations.contracts",
            "kairospy.data",
            "kairospy.execution",
            "kairospy.analytics.features",
            "kairospy.products.common.lifecycle",
            "kairospy.market_data",
            "kairospy.orchestration",
            "kairospy.integrations.ports",
            "kairospy.analytics.pricing",
            "kairospy.products",
            "kairospy.reference",
            "kairospy.risk",
            "kairospy.runtime",
            "kairospy.infrastructure.storage",
            "kairospy.strategy",
            "kairospy.trading",
            "kairospy.portfolio.treasury",
            "kairospy.research.validation",
            "kairospy.analytics.volatility",
        }
        violations: list[str] = []
        for path in sorted(IDENTITY.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names: tuple[str, ...] = ()
                if isinstance(node, ast.Import):
                    names = tuple(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = (node.module,)
                for name in names:
                    if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden):
                        violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: {name}")
        self.assertEqual(violations, [], "identity owner has upper-layer dependencies:\n" + "\n".join(violations))

    def test_identity_owner_exports_account_ref_not_account_key(self) -> None:
        from kairospy.identity import AccountRef, AccountType, InstitutionId

        account = AccountRef(InstitutionId("IBKR"), "U123", AccountType.SECURITIES_MARGIN)
        self.assertEqual(account.value, "ibkr:securities_margin:U123")
        self.assertEqual(AccountRef.__module__, "kairospy.identity.accounts")

    def test_trading_fact_and_product_lifecycle_modules_are_removed(self) -> None:
        self.assertFalse((TRADING / "__init__.py").exists())
        self.assertFalse((TRADING / "capability.py").exists())
        self.assertFalse((TRADING / "corporate_action.py").exists())
        self.assertFalse((TRADING / "derivative_event.py").exists())
        self.assertFalse((TRADING / "event.py").exists())
        self.assertFalse((TRADING / "identity.py").exists())
        self.assertFalse((TRADING / "intent.py").exists())
        self.assertFalse((TRADING / "order.py").exists())
        self.assertFalse((TRADING / "execution.py").exists())
        self.assertFalse((TRADING / "ledger.py").exists())
        self.assertFalse((TRADING / "market_data.py").exists())
        self.assertFalse((TRADING / "market_state.py").exists())
        self.assertFalse((TRADING / "product.py").exists())
        self.assertFalse((TRADING / "strategy_contract.py").exists())
        violations = []
        for path in sorted((ROOT / "kairospy").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if (
                "kairospy.trading" in text
                or "kairospy.trading.capability" in text
                or "kairospy.trading.event" in text
                or "kairospy.trading.corporate_action" in text
                or "kairospy.trading.derivative_event" in text
                or "kairospy.trading.identity" in text
                or "kairospy.trading.intent" in text
                or "kairospy.trading.order" in text
                or "kairospy.trading.execution" in text
                or "kairospy.trading.ledger" in text
                or "kairospy.trading.market_data" in text
                or "kairospy.trading.market_state" in text
                or "kairospy.trading.product" in text
                or "kairospy.trading.strategy_contract" in text
                or "AccountKey" in text
            ):
                violations.append(str(path.relative_to(ROOT)))
        self.assertEqual(violations, [], "old trading package/capability/lifecycle/event/identity/intent/order/execution/ledger/market_data/market_state/product/strategy contract imports or names remain:\n" + "\n".join(violations))

    def test_execution_owner_exports_order_and_fill_models(self) -> None:
        from kairospy.execution import (
            ExecutionCapabilities,
            ExecutionInstructions,
            Fill,
            LegFill,
            MarginMode,
            Order,
            OrderLeg,
            OrderStatus,
            OrderType,
            PositionMode,
            Settlement,
            TimeInForce,
            TradeExecution,
            TradeSide,
        )

        self.assertEqual(TradeSide.__module__, "kairospy.execution.events")
        self.assertEqual(TradeExecution.__module__, "kairospy.execution.events")
        self.assertEqual(ExecutionCapabilities.__module__, "kairospy.execution.orders")
        self.assertEqual(ExecutionInstructions.__module__, "kairospy.execution.orders")
        self.assertEqual(MarginMode.__module__, "kairospy.execution.orders")
        self.assertEqual(Order.__module__, "kairospy.execution.orders")
        self.assertEqual(OrderLeg.__module__, "kairospy.execution.orders")
        self.assertEqual(OrderStatus.__module__, "kairospy.execution.orders")
        self.assertEqual(OrderType.__module__, "kairospy.execution.orders")
        self.assertEqual(PositionMode.__module__, "kairospy.execution.orders")
        self.assertEqual(TimeInForce.__module__, "kairospy.execution.orders")
        self.assertEqual(Fill.__module__, "kairospy.execution.fills")
        self.assertEqual(LegFill.__module__, "kairospy.execution.fills")
        self.assertEqual(Settlement.__module__, "kairospy.execution.fills")

    def test_portfolio_owner_exports_ledger_accounting_and_treasury(self) -> None:
        from kairospy.portfolio.accounting.conversion import AssetConversionGraph, ConversionRate
        from kairospy.portfolio.accounting.ledger import LedgerService
        from kairospy.portfolio.accounting.portfolio import Portfolio, PortfolioSnapshot, Position
        from kairospy.portfolio.projection import portfolio_view_from_snapshot
        from kairospy.portfolio import DividendPayment, FundingPayment, Ledger, LedgerBook, LedgerEntry, LedgerEntryType, LedgerTransaction
        from kairospy.portfolio.treasury import (
            FeePolicy,
            TransferOperation,
            TransferOperationStore,
            TransferStatus,
            TreasuryLedgerPostingService,
        )

        self.assertFalse(ACCOUNTING.exists())
        self.assertFalse(TREASURY.exists())
        self.assertTrue((PORTFOLIO / "ledger.py").exists())
        self.assertTrue((PORTFOLIO / "ledger_events.py").exists())
        self.assertTrue((PORTFOLIO / "projection.py").exists())
        self.assertTrue((PORTFOLIO_ACCOUNTING / "ledger.py").exists())
        self.assertTrue((PORTFOLIO_ACCOUNTING / "portfolio.py").exists())
        self.assertTrue((PORTFOLIO_TREASURY / "transfer_contracts.py").exists())
        self.assertTrue((PORTFOLIO_TREASURY / "state_machine.py").exists())
        self.assertEqual(Ledger.__module__, "kairospy.portfolio.ledger")
        self.assertEqual(LedgerBook.__module__, "kairospy.portfolio.ledger")
        self.assertEqual(LedgerEntry.__module__, "kairospy.portfolio.ledger")
        self.assertEqual(LedgerEntryType.__module__, "kairospy.portfolio.ledger")
        self.assertEqual(LedgerTransaction.__module__, "kairospy.portfolio.ledger")
        self.assertEqual(FundingPayment.__module__, "kairospy.portfolio.ledger_events")
        self.assertEqual(DividendPayment.__module__, "kairospy.portfolio.ledger_events")
        self.assertEqual(AssetConversionGraph.__module__, "kairospy.portfolio.accounting.conversion")
        self.assertEqual(ConversionRate.__module__, "kairospy.portfolio.accounting.conversion")
        self.assertEqual(LedgerService.__module__, "kairospy.portfolio.accounting.ledger")
        self.assertEqual(portfolio_view_from_snapshot.__module__, "kairospy.portfolio.projection")
        self.assertEqual(Portfolio.__module__, "kairospy.portfolio.accounting.portfolio")
        self.assertEqual(PortfolioSnapshot.__module__, "kairospy.portfolio.accounting.portfolio")
        self.assertEqual(Position.__module__, "kairospy.portfolio.accounting.portfolio")
        self.assertEqual(FeePolicy.__module__, "kairospy.portfolio.treasury.transfer_contracts")
        self.assertEqual(TransferOperation.__module__, "kairospy.portfolio.treasury.transfer_contracts")
        self.assertEqual(TransferStatus.__module__, "kairospy.portfolio.treasury.transfer_contracts")
        self.assertEqual(TransferOperationStore.__module__, "kairospy.portfolio.treasury.state_machine")
        self.assertEqual(TreasuryLedgerPostingService.__module__, "kairospy.portfolio.treasury.ledger_posting")

    def test_old_portfolio_subcapability_packages_are_removed(self) -> None:
        violations = []
        forbidden = ("kairospy.accounting", "kairospy.treasury")
        for path in sorted((ROOT / "kairospy").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                if token in text:
                    violations.append(f"{path.relative_to(ROOT)}: {token}")
        self.assertEqual(violations, [], "old portfolio subcapability package imports remain:\n" + "\n".join(violations))

    def test_reference_owner_exports_product_contract_models(self) -> None:
        from kairospy.reference import (
            ContractType,
            CryptoOptionSpec,
            CryptoSpotSpec,
            EquitySpec,
            ExerciseStyle,
            FutureSpec,
            IndexSpec,
            InstrumentContractSpec,
            ListedOptionSpec,
            OptionRight,
            OptionSpec,
            PerpetualSpec,
            ProductType,
            ReferenceCapabilities,
            SettlementSession,
            SettlementType,
            TokenizedEquitySpec,
            is_option_spec,
            option_multiplier,
        )

        self.assertTrue((REFERENCE / "contracts.py").exists())
        for item in (
            ContractType,
            CryptoOptionSpec,
            CryptoSpotSpec,
            EquitySpec,
            ExerciseStyle,
            FutureSpec,
            IndexSpec,
            InstrumentContractSpec,
            ListedOptionSpec,
            OptionRight,
            OptionSpec,
            PerpetualSpec,
            ProductType,
            ReferenceCapabilities,
            SettlementSession,
            SettlementType,
            TokenizedEquitySpec,
            is_option_spec,
            option_multiplier,
        ):
            self.assertEqual(item.__module__, "kairospy.reference.contracts")

    def test_market_owner_exports_market_fact_models(self) -> None:
        from kairospy.market import (
            Bar,
            BoundedEventChannel,
            CanonicalBarSeriesProjection,
            CanonicalOrderBookProjection,
            CanonicalQuoteProjection,
            CapturePolicy,
            ChannelMetrics,
            ConflatedLatestChannel,
            ConsumerGap,
            DataQualityIssue,
            DayCount,
            DerivativeMarketState,
            DeliveryMode,
            DividendInput,
            EventEnvelope,
            EventQualityIssue,
            EventQualityReport,
            EventSource,
            ForwardEstimate,
            ForwardMethod,
            FundingRate,
            Greeks,
            GreeksUpdated,
            IndexPrice,
            InstrumentMarketState,
            InstrumentSnapshot,
            IterableEventSource,
            MarkPrice,
            MarketDataCapabilities,
            MarketDataKind,
            MarketDataRequirement,
            MarketEvent,
            MarketEventEnvelope,
            MarketInstrumentSlice,
            MarketPayload,
            MarketReplayDataset,
            MarketState,
            MarketEventType,
            MarketQualityIssue,
            MarketSlice,
            MarketSliceQualityIssue,
            MarketSnapshot,
            MarketSnapshotFeed,
            MarketSnapshotReplayFeed,
            OpenInterest,
            OptionChain,
            OptionChainDiscovered,
            OptionMarketObservation,
            OrderBookDelta,
            OrderBookGap,
            OrderBookLevel,
            OrderBookSnapshot,
            OrderBookState,
            OverflowPolicy,
            Quote,
            QuoteState,
            QuoteUpdated,
            QualitySeverity,
            RateCurve,
            RateNode,
            PlannedSubscription,
            StreamClosed,
            StreamOverflow,
            SubscriptionAction,
            SubscriptionCommand,
            SubscriptionKey,
            SubscriptionPlan,
            SubscriptionPlanner,
            SubscriptionReconciler,
            Trade,
            TradeUpdated,
            TradingState,
            TradingStatus,
            UnderlyingPriceUpdated,
            VolatilitySurfacePoint,
            apply_market_event,
            blocking_issues,
            cost_of_carry_forward,
            envelope,
            parity_forward,
            require_publishable,
            validate_option_observation,
            validate_events,
            zero_rate,
        )

        self.assertTrue((MARKET / "events.py").exists())
        self.assertTrue((MARKET / "capture.py").exists())
        self.assertTrue((MARKET / "forward.py").exists())
        self.assertTrue((MARKET / "projections.py").exists())
        self.assertTrue((MARKET / "quality.py").exists())
        self.assertTrue((MARKET / "repository.py").exists())
        self.assertTrue((MARKET / "soak.py").exists())
        self.assertTrue((MARKET / "source_events.py").exists())
        self.assertTrue((MARKET / "source_quality.py").exists())
        self.assertTrue((MARKET / "slices.py").exists())
        self.assertTrue((MARKET / "snapshots.py").exists())
        self.assertTrue((MARKET / "state.py").exists())
        self.assertTrue((MARKET / "stream.py").exists())
        self.assertTrue((MARKET / "subscriptions.py").exists())
        self.assertTrue((MARKET / "types.py").exists())
        self.assertFalse((MARKET_DATA / "__init__.py").exists())
        self.assertFalse((MARKET_DATA / "capture.py").exists())
        self.assertFalse((MARKET_DATA / "events.py").exists())
        self.assertFalse((MARKET_DATA / "forward.py").exists())
        self.assertFalse((MARKET_DATA / "projections.py").exists())
        self.assertFalse((MARKET_DATA / "quality.py").exists())
        self.assertFalse((MARKET_DATA / "quality_gate.py").exists())
        self.assertFalse((MARKET_DATA / "repository.py").exists())
        self.assertFalse((MARKET_DATA / "soak.py").exists())
        self.assertFalse((MARKET_DATA / "stream.py").exists())
        self.assertFalse((MARKET_DATA / "subscriptions.py").exists())
        self.assertFalse((MARKET_DATA / "types.py").exists())
        for item in (
            Bar,
            BoundedEventChannel,
            CanonicalBarSeriesProjection,
            CanonicalOrderBookProjection,
            CanonicalQuoteProjection,
            CapturePolicy,
            ChannelMetrics,
            ConflatedLatestChannel,
            ConsumerGap,
            DataQualityIssue,
            DayCount,
            DerivativeMarketState,
            DeliveryMode,
            DividendInput,
            EventEnvelope,
            EventQualityIssue,
            EventQualityReport,
            EventSource,
            ForwardEstimate,
            ForwardMethod,
            FundingRate,
            Greeks,
            GreeksUpdated,
            IndexPrice,
            InstrumentMarketState,
            InstrumentSnapshot,
            IterableEventSource,
            MarkPrice,
            MarketDataCapabilities,
            MarketDataKind,
            MarketDataRequirement,
            MarketEventEnvelope,
            MarketInstrumentSlice,
            MarketReplayDataset,
            MarketState,
            MarketEventType,
            MarketQualityIssue,
            MarketSlice,
            MarketSliceQualityIssue,
            MarketSnapshot,
            MarketSnapshotFeed,
            MarketSnapshotReplayFeed,
            OpenInterest,
            OptionChain,
            OptionChainDiscovered,
            OptionMarketObservation,
            OrderBookDelta,
            OrderBookGap,
            OrderBookLevel,
            OrderBookSnapshot,
            OrderBookState,
            OverflowPolicy,
            Quote,
            QuoteState,
            QuoteUpdated,
            QualitySeverity,
            RateCurve,
            RateNode,
            PlannedSubscription,
            StreamClosed,
            StreamOverflow,
            SubscriptionAction,
            SubscriptionCommand,
            SubscriptionKey,
            SubscriptionPlan,
            SubscriptionPlanner,
            SubscriptionReconciler,
            Trade,
            TradeUpdated,
            TradingState,
            TradingStatus,
            UnderlyingPriceUpdated,
            VolatilitySurfacePoint,
            apply_market_event,
            blocking_issues,
            cost_of_carry_forward,
            envelope,
            parity_forward,
            require_publishable,
            validate_option_observation,
            validate_events,
            zero_rate,
        ):
            self.assertTrue(item.__module__.startswith("kairospy.market."))
        self.assertIsNotNone(MarketEvent)
        self.assertIsNotNone(MarketPayload)

    def test_market_owner_exports_runtime_artifact_modules(self) -> None:
        from kairospy.market.capture import CanonicalCaptureWriter, CapturedCanonicalEventSource
        from kairospy.market.repository import ParquetMarketEventRepository
        from kairospy.market.soak import MarketDataSoakResult, run_binance_market_soak

        self.assertEqual(CanonicalCaptureWriter.__module__, "kairospy.market.capture")
        self.assertEqual(CapturedCanonicalEventSource.__module__, "kairospy.market.capture")
        self.assertEqual(ParquetMarketEventRepository.__module__, "kairospy.market.repository")
        self.assertEqual(MarketDataSoakResult.__module__, "kairospy.market.soak")
        self.assertEqual(run_binance_market_soak.__module__, "kairospy.market.soak")

    def test_old_market_data_package_is_removed(self) -> None:
        violations = []
        for path in sorted((ROOT / "kairospy").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "kairospy.market_data" in text:
                violations.append(str(path.relative_to(ROOT)))
        self.assertEqual(violations, [], "old market_data package imports remain:\n" + "\n".join(violations))

    def test_market_canonical_contracts_are_owned_by_market_domain(self) -> None:
        from kairospy.market.canonical import CanonicalEventEnvelope, MarketEventKind
        from kairospy.integrations.contracts import CanonicalEventEnvelope as CompatibilityEnvelope

        self.assertEqual(CanonicalEventEnvelope.__module__, "kairospy.market.canonical")
        self.assertEqual(MarketEventKind.__module__, "kairospy.market.canonical")
        self.assertIs(CompatibilityEnvelope, CanonicalEventEnvelope)

    def test_integration_and_governance_events_are_not_market_events(self) -> None:
        from kairospy.market.canonical import CanonicalEventEnvelope, MarketEventKind
        from kairospy.integrations.connectors.simulated import SimulatedExecutionAccountGateway
        from kairospy.governance import DataWarningRaised
        from kairospy.integrations import (
            BrokerConnected,
            BrokerDisconnected,
            LiveMarketEventSourceBinding,
            LiveProviderPorts,
            build_live_market_event_source,
            build_live_provider_ports,
            parse_account_ref,
        )
        from kairospy.integrations.ports import Environment, ExecutionPort

        self.assertFalse(CONNECTORS.exists())
        self.assertFalse(PORTS.exists())
        self.assertFalse(CONTRACTS.exists())
        self.assertTrue((INTEGRATIONS / "connectors").exists())
        self.assertTrue((INTEGRATIONS / "ports").exists())
        self.assertTrue((INTEGRATIONS / "contracts").exists())
        self.assertTrue((INTEGRATIONS / "live_ports.py").exists())
        self.assertEqual(BrokerConnected.__module__, "kairospy.integrations.events")
        self.assertEqual(BrokerDisconnected.__module__, "kairospy.integrations.events")
        self.assertEqual(LiveMarketEventSourceBinding.__module__, "kairospy.integrations.live_ports")
        self.assertEqual(LiveProviderPorts.__module__, "kairospy.integrations.live_ports")
        self.assertEqual(build_live_market_event_source.__module__, "kairospy.integrations.live_ports")
        self.assertEqual(build_live_provider_ports.__module__, "kairospy.integrations.live_ports")
        self.assertEqual(parse_account_ref.__module__, "kairospy.integrations.live_ports")
        self.assertEqual(SimulatedExecutionAccountGateway.__module__, "kairospy.integrations.connectors.simulated")
        self.assertEqual(Environment.__module__, "kairospy.environment")
        self.assertEqual(ExecutionPort.__module__, "kairospy.execution.ports")
        self.assertEqual(CanonicalEventEnvelope.__module__, "kairospy.market.canonical")
        self.assertEqual(MarketEventKind.__module__, "kairospy.market.canonical")
        self.assertEqual(DataWarningRaised.__module__, "kairospy.governance.events")

    def test_old_integration_boundary_packages_are_removed(self) -> None:
        violations = []
        forbidden = ("kairospy.connectors", "kairospy.ports", "kairospy.contracts")
        for path in sorted((ROOT / "kairospy").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                if token in text:
                    violations.append(f"{path.relative_to(ROOT)}: {token}")
        self.assertEqual(violations, [], "old integration boundary package imports remain:\n" + "\n".join(violations))

    def test_product_owner_exports_product_lifecycle_models(self) -> None:
        from kairospy.products.common import CalendarRegistry, TradingCalendar, TradingSession
        from kairospy.products.common.lifecycle import (
            AssetFlow,
            DerivativeEventType,
            DerivativePositionEvent,
            PositionFlow,
            SettlementResolution,
            SettlementResolver,
        )
        from kairospy.products.equity import (
            CashDividendEvent,
            CorporateActionService,
            CorporateActionType,
            DelistingEvent,
            InstrumentExchangeEvent,
            SplitEvent,
            StockDividendEvent,
            SymbolChangeEvent,
        )

        self.assertTrue((PRODUCTS / "common" / "calendars.py").exists())
        self.assertTrue((PRODUCTS / "equity" / "corporate_actions.py").exists())
        self.assertTrue((PRODUCTS / "common" / "lifecycle" / "derivatives.py").exists())
        self.assertTrue((PRODUCTS / "common" / "lifecycle" / "settlement.py").exists())
        self.assertEqual(TradingCalendar.__module__, "kairospy.products.common.calendars")
        self.assertEqual(TradingSession.__module__, "kairospy.products.common.calendars")
        self.assertEqual(CalendarRegistry.__module__, "kairospy.products.common.calendars")
        self.assertFalse((BACKTEST / "calendar.py").exists())
        self.assertFalse(LIFECYCLE.exists())
        violations = []
        for path in sorted((ROOT / "kairospy").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "kairospy.backtest.calendar" in text:
                violations.append(str(path.relative_to(ROOT)))
        self.assertEqual(violations, [], "old backtest calendar imports remain:\n" + "\n".join(violations))
        for item in (
            CashDividendEvent,
            CorporateActionService,
            CorporateActionType,
            DelistingEvent,
            InstrumentExchangeEvent,
            SplitEvent,
            StockDividendEvent,
            SymbolChangeEvent,
        ):
            self.assertEqual(item.__module__, "kairospy.products.equity.corporate_actions")
        self.assertEqual(AssetFlow.__module__, "kairospy.products.common.lifecycle.settlement")
        self.assertEqual(DerivativeEventType.__module__, "kairospy.products.common.lifecycle.derivatives")
        self.assertEqual(DerivativePositionEvent.__module__, "kairospy.products.common.lifecycle.derivatives")
        self.assertEqual(PositionFlow.__module__, "kairospy.products.common.lifecycle.settlement")
        self.assertEqual(SettlementResolution.__module__, "kairospy.products.common.lifecycle.settlement")
        self.assertEqual(SettlementResolver.__module__, "kairospy.products.common.lifecycle.settlement")

    def test_old_lifecycle_package_imports_are_removed(self) -> None:
        violations = []
        for path in sorted((ROOT / "kairospy").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "kairospy.lifecycle" in text:
                violations.append(str(path.relative_to(ROOT)))
        self.assertEqual(violations, [], "old lifecycle package imports remain:\n" + "\n".join(violations))

    def test_analytics_capabilities_are_nested_under_analytics_owner(self) -> None:
        from kairospy.analytics.features import FactorRuntime, FactorSnapshot, SmaFactorRuntime
        from kairospy.analytics.pricing import PricingInput, PricingModel, PricingResult, black_scholes
        from kairospy.analytics.volatility import CalibrationStatus, SurfaceSnapshot, VolObservation, build_surface

        self.assertFalse(FEATURES.exists())
        self.assertFalse(PRICING.exists())
        self.assertFalse(VOLATILITY.exists())
        self.assertTrue((ANALYTICS / "features").exists())
        self.assertTrue((ANALYTICS / "pricing").exists())
        self.assertTrue((ANALYTICS / "volatility").exists())
        self.assertEqual(FactorRuntime.__module__, "kairospy.analytics.features.runtime")
        self.assertEqual(FactorSnapshot.__module__, "kairospy.analytics.features.runtime")
        self.assertEqual(SmaFactorRuntime.__module__, "kairospy.analytics.features.sma")
        self.assertEqual(PricingInput.__module__, "kairospy.analytics.pricing.option_pricing_contracts")
        self.assertEqual(PricingModel.__module__, "kairospy.analytics.pricing.option_pricing_contracts")
        self.assertEqual(PricingResult.__module__, "kairospy.analytics.pricing.option_pricing_contracts")
        self.assertEqual(black_scholes.__module__, "kairospy.analytics.pricing.black")
        self.assertEqual(CalibrationStatus.__module__, "kairospy.analytics.volatility.contracts")
        self.assertEqual(SurfaceSnapshot.__module__, "kairospy.analytics.volatility.contracts")
        self.assertEqual(VolObservation.__module__, "kairospy.analytics.volatility.contracts")
        self.assertEqual(build_surface.__module__, "kairospy.analytics.volatility.surface")

    def test_old_analytics_capability_packages_are_removed(self) -> None:
        violations = []
        forbidden = ("kairospy.features", "kairospy.pricing", "kairospy.volatility")
        for path in sorted((ROOT / "kairospy").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                if token in text:
                    violations.append(f"{path.relative_to(ROOT)}: {token}")
        self.assertEqual(violations, [], "old analytics capability package imports remain:\n" + "\n".join(violations))

    def test_analytics_and_risk_do_not_import_backtest_profile_inputs(self) -> None:
        violations = []
        for root in (ANALYTICS, RISK):
            for path in sorted(root.rglob("*.py")):
                text = path.read_text(encoding="utf-8")
                if "kairospy.runtime.profiles.backtest" in text:
                    violations.append(str(path.relative_to(ROOT)))
        self.assertEqual(violations, [], "analytics/risk must depend on Market owner inputs, not BacktestProfile snapshots:\n" + "\n".join(violations))

    def test_strategy_archetype_risk_policies_live_in_extensions(self) -> None:
        from kairospy.risk.extensions.covered_call import (
            CoveredCallCollateralEvidence,
            CoveredCallCollateralRequest,
            covered_call_collateral_evidence,
            validate_covered_call_collateral,
        )

        self.assertFalse((RISK / "covered_call.py").exists())
        self.assertTrue((RISK / "extensions" / "covered_call.py").exists())
        self.assertEqual(CoveredCallCollateralEvidence.__module__, "kairospy.risk.extensions.covered_call")
        self.assertEqual(CoveredCallCollateralRequest.__module__, "kairospy.risk.extensions.covered_call")
        self.assertEqual(covered_call_collateral_evidence.__module__, "kairospy.risk.extensions.covered_call")
        self.assertEqual(validate_covered_call_collateral.__module__, "kairospy.risk.extensions.covered_call")
        violations = []
        for path in sorted(RISK.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "kairospy.strategy.archetypes" in text:
                violations.append(str(path.relative_to(ROOT)))
        self.assertEqual(violations, [], "risk extensions must consume archetype-neutral requests, not strategy archetype models:\n" + "\n".join(violations))

    def test_infrastructure_owner_exports_configuration_and_storage_primitives(self) -> None:
        from kairospy.infrastructure.configuration import ConfigError, DEFAULT_LAKE_ROOT, KairosProjectConfig, set_config_value
        from kairospy.infrastructure.storage.source_cache import SourceCacheEntry, SourceCacheStore
        from kairospy.infrastructure.storage.codec import event_from_primitive, event_to_primitive, from_primitive, to_primitive
        from kairospy.infrastructure.storage.data_lake import sha256_bytes, utc_midnight, write_daily_dataset, write_json
        from kairospy.infrastructure.storage.repository import FileOptionCaptureRepository, RunManifest, RunStatus, new_manifest

        self.assertFalse(STORAGE.exists())
        self.assertFalse((ROOT / "kairospy" / "configuration.py").exists())
        self.assertTrue((INFRASTRUCTURE / "configuration.py").exists())
        self.assertTrue((INFRASTRUCTURE / "storage" / "__init__.py").exists())
        self.assertTrue((INFRASTRUCTURE / "storage" / "codec.py").exists())
        self.assertTrue((INFRASTRUCTURE / "storage" / "data_lake.py").exists())
        self.assertTrue((INFRASTRUCTURE / "storage" / "repository.py").exists())
        self.assertTrue((INFRASTRUCTURE / "storage" / "source_cache.py").exists())
        self.assertFalse((ROOT / "kairospy" / "data" / "source_cache.py").exists())
        self.assertEqual(ConfigError.__module__, "kairospy.infrastructure.configuration")
        self.assertEqual(KairosProjectConfig.__module__, "kairospy.infrastructure.configuration")
        self.assertEqual(set_config_value.__module__, "kairospy.infrastructure.configuration")
        self.assertEqual(DEFAULT_LAKE_ROOT, ".kairos/data")
        self.assertEqual(to_primitive.__module__, "kairospy.infrastructure.storage.codec")
        self.assertEqual(from_primitive.__module__, "kairospy.infrastructure.storage.codec")
        self.assertEqual(event_to_primitive.__module__, "kairospy.infrastructure.storage.codec")
        self.assertEqual(event_from_primitive.__module__, "kairospy.infrastructure.storage.codec")
        self.assertEqual(sha256_bytes.__module__, "kairospy.infrastructure.storage.data_lake")
        self.assertEqual(utc_midnight.__module__, "kairospy.infrastructure.storage.data_lake")
        self.assertEqual(write_daily_dataset.__module__, "kairospy.infrastructure.storage.data_lake")
        self.assertEqual(write_json.__module__, "kairospy.infrastructure.storage.data_lake")
        self.assertEqual(FileOptionCaptureRepository.__module__, "kairospy.infrastructure.storage.repository")
        self.assertEqual(RunManifest.__module__, "kairospy.infrastructure.storage.repository")
        self.assertEqual(RunStatus.__module__, "kairospy.infrastructure.storage.repository")
        self.assertEqual(new_manifest.__module__, "kairospy.infrastructure.storage.repository")
        self.assertEqual(SourceCacheEntry.__module__, "kairospy.infrastructure.storage.source_cache")
        self.assertEqual(SourceCacheStore.__module__, "kairospy.infrastructure.storage.source_cache")

    def test_old_storage_package_imports_are_removed(self) -> None:
        violations = []
        for path in sorted((ROOT / "kairospy").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "kairospy.storage" in text or "kairospy.data.source_cache" in text:
                violations.append(str(path.relative_to(ROOT)))
        self.assertEqual(violations, [], "old storage/source cache package imports remain:\n" + "\n".join(violations))

    def test_old_configuration_module_imports_are_removed(self) -> None:
        violations = []
        for path in sorted((ROOT / "kairospy").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "kairospy.configuration" in text:
                violations.append(str(path.relative_to(ROOT)))
        self.assertEqual(violations, [], "old configuration module imports remain:\n" + "\n".join(violations))

    def test_surface_owner_exports_user_entrypoints(self) -> None:
        from kairospy.surface.cli.main import main
        from kairospy.surface.cli.output import render_product_result, render_status_table, resolve_language
        from kairospy.surface.cli.progress import TerminalProgressMatrix
        from kairospy.surface.data_features import SurfaceFeaturePublisher, load_surface_features
        from kairospy.surface.product import Data, data_product_list
        from kairospy.surface.project import initialize_project, render_project_init
        from kairospy.surface.providers import data_product_doctor, provider_doctor, providers_list

        self.assertTrue((SURFACE / "__init__.py").exists())
        self.assertTrue((SURFACE / "cli" / "main.py").exists())
        self.assertTrue((SURFACE / "cli" / "output.py").exists())
        self.assertTrue((SURFACE / "cli" / "progress.py").exists())
        self.assertTrue((SURFACE / "product.py").exists())
        self.assertTrue((SURFACE / "data_features.py").exists())
        self.assertTrue((SURFACE / "providers.py").exists())
        self.assertTrue((SURFACE / "project.py").exists())
        self.assertFalse((ROOT / "kairospy" / "product_surface.py").exists())
        self.assertFalse((ROOT / "kairospy" / "provider_surface.py").exists())
        self.assertFalse((ROOT / "kairospy" / "data" / "surface_features.py").exists())
        self.assertFalse((ROOT / "kairospy" / "project.py").exists())
        self.assertFalse((ROOT / "kairospy" / "cli_output.py").exists())
        self.assertFalse((ROOT / "kairospy" / "cli_progress.py").exists())
        self.assertEqual(main.__module__, "kairospy.surface.cli.main")
        self.assertEqual(render_product_result.__module__, "kairospy.surface.cli.output")
        self.assertEqual(render_status_table.__module__, "kairospy.surface.cli.output")
        self.assertEqual(resolve_language.__module__, "kairospy.surface.cli.output")
        self.assertEqual(TerminalProgressMatrix.__module__, "kairospy.surface.cli.progress")
        self.assertEqual(SurfaceFeaturePublisher.__module__, "kairospy.surface.data_features")
        self.assertEqual(load_surface_features.__module__, "kairospy.surface.data_features")
        self.assertEqual(Data.__module__, "kairospy.surface.product")
        self.assertEqual(data_product_list.__module__, "kairospy.surface.product")
        self.assertEqual(initialize_project.__module__, "kairospy.surface.project")
        self.assertEqual(render_project_init.__module__, "kairospy.surface.project")
        self.assertEqual(data_product_doctor.__module__, "kairospy.surface.providers")
        self.assertEqual(provider_doctor.__module__, "kairospy.surface.providers")
        self.assertEqual(providers_list.__module__, "kairospy.surface.providers")

    def test_old_surface_module_imports_are_removed(self) -> None:
        violations = []
        forbidden = (
            "kairospy.product_surface",
            "kairospy.provider_surface",
            "kairospy.project",
            "kairospy.cli_output",
            "kairospy.cli_progress",
            "kairospy.data.surface_features",
        )
        for path in sorted((ROOT / "kairospy").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                if token in text:
                    violations.append(f"{path.relative_to(ROOT)}: {token}")
        self.assertEqual(violations, [], "old surface module imports remain:\n" + "\n".join(violations))

    def test_research_owner_exports_capture_and_validation_primitives(self) -> None:
        from kairospy.governance import GovernanceAudit, audit_governance
        from kairospy.governance import (
            GovernanceRunArtifactWriter,
            PromotionDecision,
            PromotionEvidence,
            PromotionPolicy,
            ReadinessDecision,
            ReadinessEvidence,
            ReadinessStatus,
            RunArtifact,
            RunArtifactRepository,
        )
        from kairospy.research.capture.data_store import MarketSnapshotCollectionPublisher
        from kairospy.research.capture.option_capture import OptionCaptureService
        from kairospy.research.capture.option_snapshot_analysis import OptionSnapshotAnalysis, analyze_option_snapshot
        from kairospy.research.capture.series import SeriesCaptureService, SeriesCaptureSpec
        from kairospy.research.capture.spec import MarketDataType, OptionChainCaptureSpec
        from kairospy.research.validation import (
            ExperimentValidationResult,
            GateRequirement,
            ValidationArtifactWriter,
            ValidationGate,
            ValidationLevel,
        )

        self.assertFalse(CAPTURE.exists())
        self.assertFalse(VALIDATION.exists())
        self.assertTrue((RESEARCH / "capture" / "__init__.py").exists())
        self.assertTrue((RESEARCH / "validation" / "__init__.py").exists())
        self.assertTrue((GOVERNANCE / "audit.py").exists())
        self.assertEqual(MarketSnapshotCollectionPublisher.__module__, "kairospy.research.capture.data_store")
        self.assertEqual(OptionCaptureService.__module__, "kairospy.research.capture.option_capture")
        self.assertEqual(OptionSnapshotAnalysis.__module__, "kairospy.research.capture.option_snapshot_analysis")
        self.assertEqual(analyze_option_snapshot.__module__, "kairospy.research.capture.option_snapshot_analysis")
        self.assertEqual(SeriesCaptureService.__module__, "kairospy.research.capture.series")
        self.assertEqual(SeriesCaptureSpec.__module__, "kairospy.research.capture.series")
        self.assertEqual(MarketDataType.__module__, "kairospy.research.capture.spec")
        self.assertEqual(OptionChainCaptureSpec.__module__, "kairospy.research.capture.spec")
        self.assertEqual(ExperimentValidationResult.__module__, "kairospy.research.validation.contracts")
        self.assertEqual(GateRequirement.__module__, "kairospy.research.validation.gates")
        self.assertEqual(ValidationArtifactWriter.__module__, "kairospy.research.validation.artifacts")
        self.assertEqual(ValidationGate.__module__, "kairospy.research.validation.gates")
        self.assertEqual(ValidationLevel.__module__, "kairospy.research.validation.contracts")
        self.assertEqual(GovernanceAudit.__module__, "kairospy.governance.audit")
        self.assertEqual(audit_governance.__module__, "kairospy.governance.audit")
        self.assertTrue((GOVERNANCE / "readiness.py").exists())
        self.assertTrue((GOVERNANCE / "promotion.py").exists())
        self.assertTrue((GOVERNANCE / "artifact.py").exists())
        self.assertEqual(ReadinessEvidence.__module__, "kairospy.governance.readiness")
        self.assertEqual(ReadinessDecision.__module__, "kairospy.governance.readiness")
        self.assertEqual(ReadinessStatus.__module__, "kairospy.governance.readiness")
        self.assertEqual(PromotionEvidence.__module__, "kairospy.governance.promotion")
        self.assertEqual(PromotionDecision.__module__, "kairospy.governance.promotion")
        self.assertEqual(PromotionPolicy.__module__, "kairospy.governance.promotion")
        self.assertEqual(RunArtifact.__module__, "kairospy.governance.artifact")
        self.assertEqual(RunArtifactRepository.__module__, "kairospy.governance.artifact")
        self.assertEqual(GovernanceRunArtifactWriter.__module__, "kairospy.governance.artifact")

    def test_old_capture_and_validation_package_imports_are_removed(self) -> None:
        violations = []
        forbidden = ("kairospy.capture", "kairospy.validation")
        for path in sorted((ROOT / "kairospy").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                if token in text:
                    violations.append(f"{path.relative_to(ROOT)}: {token}")
        self.assertEqual(violations, [], "old capture/validation package imports remain:\n" + "\n".join(violations))

    def test_runtime_owner_exports_run_composition_and_service_supervisor(self) -> None:
        from kairospy.runtime import (
            AsyncServiceSupervisor,
            BoundRunProfile,
            CompositeRecoveryBinding,
            DurableOutboxCommandSubmitter,
            EventSourceRunEventProvider,
            ExecutionRecoveryBinding,
            ExecutionPortCommandSubmitter,
            IterableRunEventProvider,
            LiveRuntimeBindingConfig,
            LiveRuntimeComponents,
            LiveRunDaemon,
            LiveRunDaemonPhase,
            LiveRunDaemonSnapshot,
            ManagedServiceEvidenceProvider,
            ManagedServiceSpec,
            ManagedServiceStatus,
            PreparedRun,
            ProfileResult,
            RecoveryResult,
            RunArtifactLink,
            RunArtifactWriter,
            RunArtifactWriterFactory,
            RunCommandSubmitter,
            RunCommandSubmitterBinding,
            RunEventProvider,
            RunKernel,
            RunModeComposition,
            RunProfile,
            RunRequest,
            RunResult,
            RunStatus,
            RuntimeLaunchResult,
            RuntimeRunLauncher,
            RuntimeRecoveryBinding,
            RuntimeFeedPlan,
            RuntimeFeedServiceBundle,
            RuntimeFeedServicePlan,
            RunRecoveryHandler,
            ServiceCriticality,
            SubmitResult,
            backtest_composition,
            bind_live_runtime_components,
            live_runtime_profile_from_config,
            load_live_runtime_binding_config,
            paper_trading_composition,
            runtime_feed_plan,
        )

        self.assertTrue((RUNTIME / "composition.py").exists())
        self.assertTrue((RUNTIME / "bindings.py").exists())
        self.assertTrue((RUNTIME / "launch.py").exists())
        self.assertTrue((RUNTIME / "live_binding.py").exists())
        self.assertTrue((RUNTIME / "live_config.py").exists())
        self.assertTrue((RUNTIME / "live_daemon.py").exists())
        self.assertTrue((RUNTIME / "service_supervisor.py").exists())
        self.assertFalse((APPLICATION / "modes.py").exists())
        self.assertFalse((APPLICATION / "service_supervisor.py").exists())
        self.assertEqual(BoundRunProfile.__module__, "kairospy.runtime.kernel")
        self.assertEqual(CompositeRecoveryBinding.__module__, "kairospy.runtime.bindings")
        self.assertEqual(DurableOutboxCommandSubmitter.__module__, "kairospy.runtime.bindings")
        self.assertEqual(EventSourceRunEventProvider.__module__, "kairospy.runtime.bindings")
        self.assertEqual(ExecutionRecoveryBinding.__module__, "kairospy.runtime.bindings")
        self.assertEqual(ExecutionPortCommandSubmitter.__module__, "kairospy.runtime.bindings")
        self.assertEqual(IterableRunEventProvider.__module__, "kairospy.runtime.kernel")
        self.assertEqual(LiveRuntimeBindingConfig.__module__, "kairospy.runtime.live_config")
        self.assertEqual(LiveRuntimeComponents.__module__, "kairospy.runtime.live_binding")
        self.assertEqual(LiveRunDaemon.__module__, "kairospy.runtime.live_daemon")
        self.assertEqual(LiveRunDaemonPhase.__module__, "kairospy.runtime.live_daemon")
        self.assertEqual(LiveRunDaemonSnapshot.__module__, "kairospy.runtime.live_daemon")
        self.assertEqual(ManagedServiceEvidenceProvider.__module__, "kairospy.runtime.bindings")
        self.assertEqual(PreparedRun.__module__, "kairospy.runtime.kernel")
        self.assertEqual(ProfileResult.__module__, "kairospy.runtime.kernel")
        self.assertEqual(RecoveryResult.__module__, "kairospy.runtime.kernel")
        self.assertEqual(RunArtifactLink.__module__, "kairospy.runtime.kernel")
        self.assertEqual(RunArtifactWriter.__module__, "kairospy.runtime.kernel")
        self.assertIsNotNone(RunArtifactWriterFactory)
        self.assertEqual(RunCommandSubmitterBinding.__module__, "kairospy.runtime.kernel")
        self.assertEqual(RunKernel.__module__, "kairospy.runtime.kernel")
        self.assertEqual(RunModeComposition.__module__, "kairospy.runtime.composition")
        self.assertEqual(RunProfile.__module__, "kairospy.runtime.kernel")
        self.assertEqual(RunRequest.__module__, "kairospy.runtime.kernel")
        self.assertEqual(RunResult.__module__, "kairospy.runtime.kernel")
        self.assertEqual(RunStatus.__module__, "kairospy.runtime.kernel")
        self.assertEqual(RuntimeLaunchResult.__module__, "kairospy.runtime.launch")
        self.assertEqual(RuntimeRunLauncher.__module__, "kairospy.runtime.launch")
        self.assertEqual(RuntimeRecoveryBinding.__module__, "kairospy.runtime.kernel")
        self.assertEqual(RuntimeFeedPlan.__module__, "kairospy.runtime.composition")
        self.assertEqual(RuntimeFeedServiceBundle.__module__, "kairospy.runtime.composition")
        self.assertEqual(RuntimeFeedServicePlan.__module__, "kairospy.runtime.composition")
        self.assertEqual(backtest_composition.__module__, "kairospy.runtime.composition")
        self.assertEqual(bind_live_runtime_components.__module__, "kairospy.runtime.live_binding")
        self.assertEqual(live_runtime_profile_from_config.__module__, "kairospy.runtime.live_config")
        self.assertEqual(load_live_runtime_binding_config.__module__, "kairospy.runtime.live_config")
        self.assertEqual(paper_trading_composition.__module__, "kairospy.runtime.composition")
        self.assertEqual(runtime_feed_plan.__module__, "kairospy.runtime.composition")
        self.assertEqual(AsyncServiceSupervisor.__module__, "kairospy.runtime.service_supervisor")
        self.assertEqual(ManagedServiceSpec.__module__, "kairospy.runtime.service_supervisor")
        self.assertEqual(ManagedServiceStatus.__module__, "kairospy.runtime.service_supervisor")
        self.assertEqual(ServiceCriticality.__module__, "kairospy.runtime.service_supervisor")
        self.assertEqual(SubmitResult.__module__, "kairospy.runtime.kernel")
        self.assertIsNotNone(RunCommandSubmitter)
        self.assertIsNotNone(RunEventProvider)
        self.assertIsNotNone(RunRecoveryHandler)

    def test_application_package_is_split_into_runtime_profiles_and_governance(self) -> None:
        from kairospy.governance.artifact import RunArtifactRepository
        from kairospy.governance.attribution import RunAttribution, build_run_attribution
        from kairospy.governance.incidents import RUNTIME_FAILURE_POLICY_ID, run_runtime_failure_policy
        from kairospy.runtime.application import FunctionProbe, KairosApplication, RuntimeStatus
        from kairospy.runtime.async_runtime import AsyncKairosRuntime
        from kairospy.runtime.clock import Clock, FixedClock, SystemClock
        from kairospy.runtime.config import ApplicationConfig, RuntimePaths
        from kairospy.runtime.kernel import GovernedStrategyRunLoop, RunKernel, RunRequest, RunResult, StrategyRunResult
        from kairospy.runtime.profiles.backtest import BacktestProfile, backtest_profile
        from kairospy.runtime.profiles.backtest.immediate import run_immediate_target_backtest
        from kairospy.runtime.profiles.live import LiveProfile, live_profile
        from kairospy.runtime.profiles.simulation import (
            SimulationProfile,
            exchange_testnet_simulation_profile,
            historical_replay_simulation_profile,
            paper_simulation_profile,
        )
        from kairospy.runtime.profiles.live.reference_artifact import (
            RUNTIME_REFERENCE_SCENARIO_ID,
            run_runtime_reference_artifact,
        )
        from kairospy.runtime.recovery import RuntimeRecoveryResult, RuntimeRecoveryService
        from kairospy.runtime.supervisor import RuntimeSupervisor, write_soak_artifact

        self.assertFalse(APPLICATION.exists())
        self.assertTrue((RUNTIME / "application.py").exists())
        self.assertTrue((RUNTIME / "async_runtime.py").exists())
        self.assertTrue((RUNTIME / "clock.py").exists())
        self.assertTrue((RUNTIME / "config.py").exists())
        self.assertTrue((RUNTIME / "kernel.py").exists())
        self.assertTrue((RUNTIME / "recovery.py").exists())
        self.assertTrue((RUNTIME / "supervisor.py").exists())
        self.assertTrue((BACKTEST_PROFILE / "profile.py").exists())
        self.assertTrue((BACKTEST_PROFILE / "immediate.py").exists())
        self.assertTrue((SIMULATION_PROFILE / "__init__.py").exists())
        self.assertTrue((SIMULATION_PROFILE / "profile.py").exists())
        self.assertTrue((RUNTIME / "profiles" / "live" / "profile.py").exists())
        self.assertTrue((RUNTIME / "profiles" / "live" / "reference_artifact.py").exists())
        self.assertTrue((GOVERNANCE / "artifact.py").exists())
        self.assertTrue((GOVERNANCE / "attribution.py").exists())
        self.assertTrue((GOVERNANCE / "incidents.py").exists())
        self.assertEqual(Clock.__module__, "kairospy.runtime.clock")
        self.assertEqual(FixedClock.__module__, "kairospy.runtime.clock")
        self.assertEqual(SystemClock.__module__, "kairospy.runtime.clock")
        self.assertEqual(ApplicationConfig.__module__, "kairospy.runtime.config")
        self.assertEqual(RuntimePaths.__module__, "kairospy.runtime.config")
        self.assertEqual(FunctionProbe.__module__, "kairospy.runtime.application")
        self.assertEqual(KairosApplication.__module__, "kairospy.runtime.application")
        self.assertEqual(RuntimeStatus.__module__, "kairospy.runtime.application")
        self.assertEqual(AsyncKairosRuntime.__module__, "kairospy.runtime.async_runtime")
        self.assertEqual(RuntimeRecoveryResult.__module__, "kairospy.runtime.recovery")
        self.assertEqual(RuntimeRecoveryService.__module__, "kairospy.runtime.recovery")
        self.assertEqual(RuntimeSupervisor.__module__, "kairospy.runtime.supervisor")
        self.assertEqual(write_soak_artifact.__module__, "kairospy.runtime.supervisor")
        self.assertEqual(GovernedStrategyRunLoop.__module__, "kairospy.runtime.kernel")
        self.assertEqual(RunKernel.__module__, "kairospy.runtime.kernel")
        self.assertEqual(RunRequest.__module__, "kairospy.runtime.kernel")
        self.assertEqual(RunResult.__module__, "kairospy.runtime.kernel")
        self.assertEqual(StrategyRunResult.__module__, "kairospy.runtime.kernel")
        self.assertEqual(BacktestProfile.__module__, "kairospy.runtime.profiles.backtest.profile")
        self.assertEqual(backtest_profile.__module__, "kairospy.runtime.profiles.backtest.profile")
        self.assertEqual(run_immediate_target_backtest.__module__, "kairospy.runtime.profiles.backtest.immediate")
        self.assertEqual(LiveProfile.__module__, "kairospy.runtime.profiles.live.profile")
        self.assertEqual(live_profile.__module__, "kairospy.runtime.profiles.live.profile")
        self.assertEqual(SimulationProfile.__module__, "kairospy.runtime.profiles.simulation.profile")
        self.assertEqual(historical_replay_simulation_profile.__module__, "kairospy.runtime.profiles.simulation.profile")
        self.assertEqual(paper_simulation_profile.__module__, "kairospy.runtime.profiles.simulation.profile")
        self.assertEqual(exchange_testnet_simulation_profile.__module__, "kairospy.runtime.profiles.simulation.profile")
        self.assertEqual(run_runtime_reference_artifact.__module__, "kairospy.runtime.profiles.live.reference_artifact")
        self.assertEqual(RUNTIME_REFERENCE_SCENARIO_ID, "runtime-l2-spot-target-position-v1")
        self.assertEqual(run_runtime_failure_policy.__module__, "kairospy.governance.incidents")
        self.assertEqual(RUNTIME_FAILURE_POLICY_ID, "runtime-l3-failure-policy-v1")
        self.assertEqual(RunArtifactRepository.__module__, "kairospy.governance.artifact")
        self.assertEqual(RunAttribution.__module__, "kairospy.governance.attribution")
        self.assertEqual(build_run_attribution.__module__, "kairospy.governance.attribution")

    def test_backtest_profile_is_runtime_owned(self) -> None:
        from kairospy.runtime.profiles.backtest.clock import BacktestClock
        from kairospy.runtime.profiles.backtest.engine import BacktestEngine
        from kairospy.market.snapshots import MarketReplayDataset, MarketSnapshot, MarketSnapshotReplayFeed
        from kairospy.runtime.profiles.backtest.fill import ListedOptionComboFillModel
        from kairospy.runtime.profiles.backtest.result import BacktestConfig, BacktestResult, ResultStatus
        from kairospy.runtime.profiles.backtest.synthetic_scenarios import build_synthetic_backtest_dataset

        self.assertFalse(BACKTEST.exists())
        self.assertTrue((BACKTEST_PROFILE / "__init__.py").exists())
        self.assertTrue((BACKTEST_PROFILE / "engine.py").exists())
        self.assertTrue((BACKTEST_PROFILE / "feed.py").exists())
        self.assertTrue((BACKTEST_PROFILE / "fill.py").exists())
        self.assertEqual(BacktestClock.__module__, "kairospy.runtime.profiles.backtest.clock")
        self.assertEqual(BacktestEngine.__module__, "kairospy.runtime.profiles.backtest.engine")
        self.assertEqual(MarketReplayDataset.__module__, "kairospy.market.snapshots")
        self.assertEqual(MarketSnapshot.__module__, "kairospy.market.snapshots")
        self.assertEqual(MarketSnapshotReplayFeed.__module__, "kairospy.market.snapshots")
        self.assertEqual(ListedOptionComboFillModel.__module__, "kairospy.runtime.profiles.backtest.fill")
        self.assertEqual(BacktestConfig.__module__, "kairospy.runtime.profiles.backtest.result")
        self.assertEqual(BacktestResult.__module__, "kairospy.runtime.profiles.backtest.result")
        self.assertEqual(ResultStatus.__module__, "kairospy.runtime.profiles.backtest.result")
        self.assertEqual(build_synthetic_backtest_dataset.__module__, "kairospy.runtime.profiles.backtest.synthetic_scenarios")

    def test_market_snapshot_release_contracts_are_not_backtest_owned(self) -> None:
        violations = []
        for path in sorted((ROOT / "kairospy").rglob("*.py")):
            if path == BACKTEST_PROFILE / "feed.py":
                continue
            text = path.read_text(encoding="utf-8")
            if "kairospy.runtime.profiles.backtest.feed" in text:
                violations.append(str(path.relative_to(ROOT)))
        self.assertEqual(violations, [], "market snapshot release contracts should import from kairospy.market.snapshots:\n" + "\n".join(violations))

    def test_old_application_package_imports_are_removed(self) -> None:
        forbidden = (
            "kairospy.application",
        )
        violations = []
        for path in sorted((ROOT / "kairospy").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                if token in text:
                    violations.append(f"{path.relative_to(ROOT)}: {token}")
        self.assertEqual(violations, [], "old application package imports remain:\n" + "\n".join(violations))

    def test_old_backtest_package_imports_are_removed(self) -> None:
        violations = []
        for path in sorted((ROOT / "kairospy").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "kairospy.backtest" in text:
                violations.append(str(path.relative_to(ROOT)))
        self.assertEqual(violations, [], "old backtest package imports remain:\n" + "\n".join(violations))

    def test_orchestration_package_is_split_into_runtime_and_governance(self) -> None:
        from kairospy.governance.kill_switch import KillSwitch
        from kairospy.governance.observability import AlertSeverity, OperationalMonitor
        from kairospy.governance.reconciliation import ReconciliationReport, ReconciliationService
        from kairospy.governance.strategy_monitoring import StrategyHealth, StrategyHealthMonitor
        from kairospy.runtime.coordinator import ExecutionCoordinator
        from kairospy.runtime.store.event_log import PersistentEventLog
        from kairospy.runtime.store.runtime_store import ManualOrderResolution, SQLiteRuntimeStore
        from kairospy.runtime.testing.faults import OneShotRuntimeFaultInjector, RuntimeFaultPoint

        self.assertFalse(ORCHESTRATION.exists())
        self.assertTrue((RUNTIME / "coordinator.py").exists())
        self.assertTrue((RUNTIME_STORE / "event_log.py").exists())
        self.assertTrue((RUNTIME_STORE / "runtime_store.py").exists())
        self.assertTrue((RUNTIME_TESTING / "faults.py").exists())
        self.assertTrue((GOVERNANCE / "kill_switch.py").exists())
        self.assertTrue((GOVERNANCE / "observability.py").exists())
        self.assertTrue((GOVERNANCE / "reconciliation.py").exists())
        self.assertTrue((GOVERNANCE / "strategy_monitoring.py").exists())
        self.assertEqual(ExecutionCoordinator.__module__, "kairospy.runtime.coordinator")
        self.assertEqual(PersistentEventLog.__module__, "kairospy.runtime.store.event_log")
        self.assertEqual(SQLiteRuntimeStore.__module__, "kairospy.runtime.store.runtime_store")
        self.assertEqual(ManualOrderResolution.__module__, "kairospy.runtime.store.runtime_store")
        self.assertEqual(OneShotRuntimeFaultInjector.__module__, "kairospy.runtime.testing.faults")
        self.assertEqual(RuntimeFaultPoint.__module__, "kairospy.runtime.testing.faults")
        self.assertEqual(KillSwitch.__module__, "kairospy.governance.kill_switch")
        self.assertEqual(AlertSeverity.__module__, "kairospy.governance.observability")
        self.assertEqual(OperationalMonitor.__module__, "kairospy.governance.observability")
        self.assertEqual(ReconciliationReport.__module__, "kairospy.governance.reconciliation")
        self.assertEqual(ReconciliationService.__module__, "kairospy.governance.reconciliation")
        self.assertEqual(StrategyHealth.__module__, "kairospy.governance.strategy_monitoring")
        self.assertEqual(StrategyHealthMonitor.__module__, "kairospy.governance.strategy_monitoring")

    def test_old_orchestration_package_imports_are_removed(self) -> None:
        violations = []
        for path in sorted((ROOT / "kairospy").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "kairospy.orchestration" in text:
                violations.append(str(path.relative_to(ROOT)))
        self.assertEqual(violations, [], "old orchestration package imports remain:\n" + "\n".join(violations))

    def test_trading_model_does_not_depend_on_upper_layers(self) -> None:
        forbidden = {
            "kairospy.portfolio.accounting",
            "kairospy.integrations.connectors",
            "kairospy.backtest",
            "kairospy." + "catalog",
            "kairospy.data",
            "kairospy.execution",
            "kairospy.analytics.features",
            "kairospy.market_data",
            "kairospy.orchestration",
            "kairospy.analytics.pricing",
            "kairospy.research.capture",
            "kairospy.risk",
            "kairospy.infrastructure.storage",
            "kairospy.analytics.volatility",
        }
        violations: list[str] = []
        for path in sorted(TRADING.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names: tuple[str, ...] = ()
                if isinstance(node, ast.Import):
                    names = tuple(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = (node.module,)
                for name in names:
                    if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden):
                        violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: {name}")
        self.assertEqual(violations, [], "trading model has upper-layer dependencies:\n" + "\n".join(violations))

    def test_strategy_runtime_contract_is_not_in_trading_model(self) -> None:
        self.assertFalse((TRADING / "strategy.py").exists())
        from dataclasses import fields as dataclass_fields
        from datetime import datetime, timezone
        from decimal import Decimal

        from kairospy.strategy import (
            BudgetView,
            CancelIntent,
            CashAndCarryIntent,
            CloseStructureIntent,
            Context,
            CoveredCallIntent,
            EconomicIntent,
            FeatureView,
            HedgeIntent,
            IntentView,
            LegIntent,
            MarketView,
            OpenStructureIntent,
            OrderView,
            PortfolioView,
            ProtectivePutIntent,
            ReferenceView,
            Strategy,
            StrategyDecision,
            StrategyLifecycle,
            StrategySpec,
            TargetExposureIntent,
            TargetPositionIntent,
            TransferIntent,
            ViewFieldSchema,
            ViewSchema,
            context_view_schemas,
            view_schema,
        )

        self.assertEqual(Context.__module__, "kairospy.strategy.protocols")
        self.assertEqual(MarketView.__module__, "kairospy.strategy.views")
        self.assertEqual(PortfolioView.__module__, "kairospy.strategy.views")
        self.assertEqual(FeatureView.__module__, "kairospy.strategy.views")
        self.assertEqual(ReferenceView.__module__, "kairospy.strategy.views")
        self.assertEqual(OrderView.__module__, "kairospy.strategy.views")
        self.assertEqual(IntentView.__module__, "kairospy.strategy.views")
        self.assertEqual(BudgetView.__module__, "kairospy.strategy.views")
        self.assertEqual(LegIntent.__module__, "kairospy.strategy.intents")
        self.assertEqual(OpenStructureIntent.__module__, "kairospy.strategy.intents")
        self.assertEqual(CloseStructureIntent.__module__, "kairospy.strategy.intents")
        self.assertEqual(TargetExposureIntent.__module__, "kairospy.strategy.intents")
        self.assertEqual(TargetPositionIntent.__module__, "kairospy.strategy.intents")
        self.assertEqual(HedgeIntent.__module__, "kairospy.strategy.intents")
        self.assertEqual(TransferIntent.__module__, "kairospy.strategy.intents")
        self.assertEqual(CancelIntent.__module__, "kairospy.strategy.intents")
        self.assertEqual(CoveredCallIntent.__module__, "kairospy.strategy.archetypes")
        self.assertEqual(ProtectivePutIntent.__module__, "kairospy.strategy.archetypes")
        self.assertEqual(CashAndCarryIntent.__module__, "kairospy.strategy.archetypes")
        self.assertEqual(EconomicIntent.__module__, "kairospy.strategy.contracts")
        self.assertEqual(StrategyLifecycle.__module__, "kairospy.strategy.contracts")
        self.assertEqual(StrategySpec.__module__, "kairospy.strategy.contracts")
        self.assertEqual(ViewFieldSchema.__module__, "kairospy.strategy.views")
        self.assertEqual(ViewSchema.__module__, "kairospy.strategy.views")
        self.assertEqual(
            tuple(Context.__dataclass_fields__),
            ("market", "portfolio", "features", "reference", "orders", "intents", "budget"),
        )
        schemas = context_view_schemas()
        self.assertEqual(Context.view_schemas(), schemas)
        self.assertEqual(
            tuple(item.view for item in schemas),
            ("MarketView", "PortfolioView", "FeatureView", "ReferenceView", "OrderView", "IntentView", "BudgetView"),
        )
        view_classes = {
            "MarketView": MarketView,
            "PortfolioView": PortfolioView,
            "FeatureView": FeatureView,
            "ReferenceView": ReferenceView,
            "OrderView": OrderView,
            "IntentView": IntentView,
            "BudgetView": BudgetView,
        }
        for schema in schemas:
            self.assertEqual(schema, view_schema(schema.view))
            self.assertEqual(schema.field_names, tuple(field.name for field in dataclass_fields(view_classes[schema.view])))
            self.assertEqual(len(schema.schema_hash), 64)
            for field_schema in schema.field_schemas:
                self.assertTrue(field_schema.time_semantics)
                self.assertTrue(field_schema.evidence)
        self.assertIn("available_time", view_schema("MarketView").field_names)
        self.assertIn("freshness_seconds", view_schema("MarketView").field_names)
        self.assertIn("data_binding", view_schema("MarketView").field_names)
        self.assertIn("event_window", view_schema("MarketView").field_names)
        self.assertIn("available_time", view_schema("FeatureView").field_names)
        self.assertIn("balances", view_schema("PortfolioView").field_names)
        self.assertIn("ledger_hash", view_schema("PortfolioView").field_names)
        self.assertIn("state_hash", view_schema("PortfolioView").field_names)
        self.assertIn("margin_requirement", view_schema("PortfolioView").field_names)
        self.assertIn("instrument_ids", view_schema("ReferenceView").field_names)
        self.assertIn("contract_summaries", view_schema("ReferenceView").field_names)
        self.assertIn("integrity_hash", view_schema("ReferenceView").field_names)
        self.assertIn("remaining_capital", view_schema("BudgetView").field_names)
        self.assertIn("decision_count", view_schema("BudgetView").field_names)
        self.assertIn("risk_decision_hash", view_schema("BudgetView").field_names)
        self.assertIn("governance_hash", view_schema("BudgetView").field_names)
        self.assertIn("state_hash", view_schema("BudgetView").field_names)
        self.assertIn("DataClient", view_schema("MarketView").forbidden_dependencies)
        self.assertIn("submit method", view_schema("OrderView").forbidden_dependencies)
        self.assertEqual(Strategy.__module__, "kairospy.strategy.protocols")
        self.assertEqual(StrategyDecision.__module__, "kairospy.strategy.protocols")
        timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        context = Context(
            MarketView(timestamp, 1, ()),
            PortfolioView(timestamp=timestamp),
            budget=BudgetView(approved_capital=Decimal("100")),
        )
        self.assertEqual(context.market.available_time, timestamp)
        self.assertEqual(context.market.data_binding, "unknown")
        self.assertEqual(context.market.event_window, (timestamp, timestamp))
        self.assertIsNone(context.features.available_time)
        self.assertEqual(context.budget.remaining_capital, Decimal("100"))
        self.assertEqual(set(context.view_hashes), {"market", "portfolio", "features", "reference", "orders", "intents", "budget"})
        self.assertEqual(len(context.context_hash), 64)
        decision = StrategyDecision.none(timestamp=timestamp, reason="runtime unavailable")
        self.assertEqual(decision.action, "none")

    def test_deleted_strategies_package_does_not_return(self) -> None:
        self.assertFalse((ROOT / "kairospy" / "strategies").exists())

    def test_json_ledger_repository_is_removed(self) -> None:
        self.assertFalse(ACCOUNTING.exists())
        self.assertFalse((PORTFOLIO_ACCOUNTING / "repository.py").exists())
        self.assertFalse((ROOT / "kairospy" / "application" / "ledger_migration.py").exists())

    def test_old_catalog_package_is_removed(self) -> None:
        self.assertFalse((ROOT / "kairospy" / "catalog").exists())
        forbidden = ("Instrument" + "Catalog", "ExternalMapping" + "Repository")
        violations = []
        for path in sorted((ROOT / "kairospy").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for name in forbidden:
                if name in text:
                    violations.append(f"{path.relative_to(ROOT)}: {name}")
        self.assertEqual(violations, [], "old catalog code remains:\n" + "\n".join(violations))

    def test_only_current_reference_and_metadata_models_exist(self) -> None:
        self.assertFalse((TRADING / "instrument.py").exists())
        self.assertFalse((ROOT / "kairospy" / "data" / ("metadata_" + "migration.py")).exists())
        self.assertFalse((ROOT / ("re" + "search") / ("btc_study_" + "governance.py")).exists())

    def test_legacy_instrument_access_is_removed(self) -> None:
        forbidden = ("definition.product_" + "spec", "definition.listings" + "[", "definition.listing" + "(")
        violations = []
        for path in sorted((ROOT / "kairospy").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                if token in text:
                    violations.append(f"{path.relative_to(ROOT)}: {token}")
        self.assertEqual(violations, [], "legacy instrument access remains:\n" + "\n".join(violations))

    def test_removed_dataset_and_surface_repositories_do_not_return(self) -> None:
        forbidden = ("DatasetRepository", "Re" + "search" + "DatasetStore", "SurfaceRepository")
        violations = []
        for path in sorted((ROOT / "kairospy").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for name in forbidden:
                if name in text:
                    violations.append(f"{path.relative_to(ROOT)}: {name}")
        self.assertEqual(violations, [], "removed data repositories remain:\n" + "\n".join(violations))
        self.assertFalse((ROOT / "kairospy" / "volatility" / "repository.py").exists())


if __name__ == "__main__":
    unittest.main()
