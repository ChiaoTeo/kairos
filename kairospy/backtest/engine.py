from __future__ import annotations

from kairospy.trading.identity import InstitutionId

from dataclasses import replace
from decimal import Decimal
import json
from uuid import NAMESPACE_URL, UUID, uuid5

from kairospy.reference.access import contract_spec, definition_at
from kairospy.trading.execution import TradeSide
from kairospy.trading.identity import AccountKey, AccountType, AssetId, VenueId
from kairospy.trading.intent import CloseStructureIntent, LegIntent
from kairospy.trading.order import Fill, Order, OrderStatus, TimeInForce
from kairospy.trading.product import is_option_spec, option_multiplier
from kairospy.pricing.option_valuation import OptionValuationService
from kairospy.capture.features import FeatureEngine, build_features
from kairospy.risk.engine import RiskDecisionType, RiskEngine
from kairospy.risk.limits import RiskLimits
from kairospy.risk.analytics import historical_var_es
from kairospy.risk.scenarios import RevaluationPosition, ScenarioEngine, standard_scenario_grid
from kairospy.storage.codec import to_primitive
from kairospy.strategy.protocols import Strategy, StrategyContext

from .clock import BacktestClock
from .execution import ExecutionPlanner
from .feed import MarketReplayDataset, MarketSnapshot, MarketSnapshotFeed, MarketSnapshotReplayFeed
from .fill import FillModelType, FixedCommissionModel, ListedOptionComboFillModel
from .metrics import calculate_metrics
from .portfolio import BacktestPortfolio
from .result import BacktestConfig, BacktestResult, EquityPoint, ResultStatus
from .settlement import due_settlements


class DeterministicIds:
    def __init__(self, namespace: str) -> None:
        self.namespace = namespace
        self.counter = 0

    def next(self) -> UUID:
        self.counter += 1
        return uuid5(NAMESPACE_URL, f"{self.namespace}:{self.counter}")


class BacktestEngine:
    def __init__(self, dataset: MarketReplayDataset | MarketSnapshotFeed, config: BacktestConfig, strategy: Strategy,
                 risk_limits: RiskLimits = RiskLimits(), *, factor_runtimes: tuple[object, ...] = ()) -> None:
        self.feed = dataset if isinstance(dataset, MarketSnapshotReplayFeed) or hasattr(dataset, "between") else MarketSnapshotReplayFeed(dataset)
        self.dataset = dataset.dataset if hasattr(dataset, "dataset") else dataset
        self.config = config
        self.strategy = strategy
        self.risk_limits = risk_limits
        self.factor_runtimes = factor_runtimes

    def run(self) -> BacktestResult:
        self._validate_inputs()
        run_material = json.dumps({
            "dataset_id": self.dataset.manifest.dataset_id,
            "dataset_hash": self.dataset.manifest.content_hash,
            "split": self.dataset.manifest.split,
            "strategy": self.strategy.strategy_id,
            "strategy_config": to_primitive(getattr(self.strategy, "config", None)),
            "factor_specs": [runtime.spec.spec_hash for runtime in self.factor_runtimes],
            "config": to_primitive(self.config),
        }, sort_keys=True)
        run_id = uuid5(NAMESPACE_URL, run_material)
        ids = DeterministicIds(str(run_id))
        catalog = self.dataset.reference_catalog()
        planner = ExecutionPlanner(catalog, id_factory=ids.next)
        commission = FixedCommissionModel(self.config.commission_per_contract, Decimal("1"), self.config.regulatory_fee_per_contract)
        fill_model = ListedOptionComboFillModel(FillModelType(self.config.fill_model), commission, catalog, id_factory=ids.next)
        risk = RiskEngine(self.risk_limits, catalog, ids.next)
        valuation_service = OptionValuationService(catalog)
        feature_engine = FeatureEngine()
        account = AccountKey(InstitutionId("backtest"), str(run_id), AccountType.SECURITIES_MARGIN)
        portfolio = BacktestPortfolio(self.config.initial_cash, catalog, account)
        clock = BacktestClock()
        orders: dict[UUID, Order] = {}
        intents, decisions, fills, settlements, snapshots, equity = [], [], [], [], [], []
        validity = []
        started = False
        last_market = None
        processed_markets = []
        reduce_only = False
        scenario_pnls = []
        valuation_total = valuation_success = surface_total = surface_calibrated = surface_arbitrage_passed = 0
        vendor_internal_delta_errors = []
        latest_factor_snapshots = ()
        for market in self.feed.between(self.config.start, self.config.end):
            clock.advance(market.timestamp)
            market, valuation = valuation_service.value(market)
            features = feature_engine.update(valuation)
            latest_factor_snapshots = tuple(
                snapshot for runtime in self.factor_runtimes
                if (snapshot := runtime.update_market(market, valuation)) is not None
            )
            valuation_total += len(valuation.instruments)
            valuation_success += sum(item.pricing is not None for item in valuation.instruments)
            if valuation.surface is not None:
                surface_total += 1
                surface_calibrated += valuation.surface.calibration_status.value == "calibrated"
                surface_arbitrage_passed += valuation.surface.diagnostics.passed
            vendor_internal_delta_errors.extend(
                item.vendor_delta_error for item in valuation.instruments if item.vendor_delta_error is not None
            )
            last_market = market
            processed_markets.append(market)
            new_fills = []
            for order_id, order in list(orders.items()):
                if order.status.terminal:
                    continue
                attempt = fill_model.attempt(order, market)
                orders[order_id] = attempt.order
                if attempt.fill:
                    portfolio.apply_fill(attempt.fill)
                    fills.append(attempt.fill)
                    new_fills.append(attempt.fill)
            for settlement in due_settlements(portfolio, self.dataset.contracts, market.timestamp):
                portfolio.apply_settlement(settlement)
                settlements.append(settlement)
            snapshot = portfolio.snapshot(market)
            scenario_pnls.extend(self._scenario_pnls(portfolio, valuation, catalog, market.timestamp))
            post_trade = risk.post_trade_violations(snapshot)
            reduce_only = bool(post_trade)
            context = StrategyContext(
                market, snapshot, tuple(order for order in orders.values() if not order.status.terminal), catalog,
                valuation, valuation.surface, features, factor_snapshots=latest_factor_snapshots,
            )
            generated = []
            if not started:
                generated.extend(self.strategy.on_start(context))
                started = True
            for fill in new_fills:
                generated.extend(self.strategy.on_fill(fill, context))
            generated.extend(self.strategy.on_market(context))
            if post_trade and snapshot.open_structures:
                closing_ids = {intent.structure_id for intent in generated if isinstance(intent, CloseStructureIntent)}
                for structure in snapshot.open_structures:
                    if structure.structure_id in closing_ids:
                        continue
                    legs = tuple(LegIntent(instrument_id, TradeSide.SELL if sign > 0 else TradeSide.BUY, abs(sign)) for instrument_id, sign in structure.legs)
                    generated.append(CloseStructureIntent(
                        structure.strategy_id, structure.structure_id, legs, structure.quantity, None,
                        TimeInForce.DAY, f"post-trade risk reduction: {','.join(post_trade)}", ids.next(),
                    ))
            for intent in generated:
                intents.append(intent)
                approved, decision = risk.evaluate(intent, snapshot, market, reduce_only=reduce_only)
                decisions.append(decision)
                if approved is not None:
                    order = planner.plan(approved, market.timestamp).transition(OrderStatus.WORKING)
                    orders[order.order_id] = order
            if post_trade:
                validity.extend(f"post_trade:{item}" for item in post_trade)
            snapshots.append(snapshot)
            equity.append(self._equity_point(snapshot))
        if last_market is None:
            raise ValueError("no market slices in configured interval")
        _, end_valuation = valuation_service.value(last_market)
        end_context = StrategyContext(
            last_market, portfolio.snapshot(last_market), tuple(order for order in orders.values() if not order.status.terminal), catalog,
            end_valuation, end_valuation.surface, build_features(end_valuation),
            factor_snapshots=latest_factor_snapshots,
        )
        for intent in self.strategy.on_end(end_context):
            intents.append(intent)
        for order_id, order in list(orders.items()):
            if not order.status.terminal:
                orders[order_id] = order.transition(OrderStatus.CANCELLED, reason="backtest_end")
        if self.config.force_close_at_end and portfolio.structures:
            forced_intents, forced_fills, forced_orders, reasons = self._force_close(portfolio, last_market, commission, planner, ids, catalog)
            intents.extend(forced_intents)
            fills.extend(forced_fills)
            orders.update({order.order_id: order for order in forced_orders})
            validity.extend(reasons)
        final_snapshot = portfolio.snapshot(last_market)
        if not snapshots or final_snapshot != snapshots[-1]:
            snapshots.append(final_snapshot)
            equity.append(self._equity_point(final_snapshot))
        if final_snapshot.unpriced_positions:
            validity.append("unpriced_positions")
        if portfolio.structures:
            validity.append("open_structures_at_end")
        expected_cash = self._reconstruct_cash(self.config.initial_cash, fills, settlements, catalog)
        cash_difference = portfolio.cash - expected_cash
        if cash_difference != 0:
            validity.append("cash_reconciliation_error")
        rejected_count = sum(item.decision is RiskDecisionType.REJECTED for item in decisions)
        metrics = calculate_metrics(tuple(equity), tuple(fills), tuple(orders.values()), rejected_count, tuple(snapshots), tuple(processed_markets), tuple(settlements), catalog)
        metrics.update({
            "data_contract_coverage": self.dataset.manifest.contract_coverage,
            "data_quote_coverage": self.dataset.manifest.quote_coverage,
            "data_greeks_coverage": self.dataset.manifest.greeks_coverage,
            "data_stale_rate": self.dataset.manifest.stale_rate,
            "unfilled_orders": sum(order.status in {OrderStatus.EXPIRED, OrderStatus.CANCELLED} for order in orders.values()),
            "uncloseable_positions": len(portfolio.structures),
            "fallback_prices": sum(item.fallback_price_count for item in snapshots),
            "synthetic_dataset": self.dataset.manifest.synthetic,
            "sample_split": self.dataset.manifest.split,
            "reconstructed_cash": expected_cash,
            "cash_reconciliation_difference": cash_difference,
            "internal_valuation_coverage": Decimal(valuation_success) / Decimal(valuation_total) if valuation_total else Decimal("1"),
            "surface_calibration_rate": Decimal(surface_calibrated) / Decimal(surface_total) if surface_total else Decimal("0"),
            "surface_arbitrage_pass_rate": Decimal(surface_arbitrage_passed) / Decimal(surface_total) if surface_total else Decimal("0"),
            "mean_vendor_internal_delta_error": sum(vendor_internal_delta_errors, Decimal("0")) / Decimal(len(vendor_internal_delta_errors)) if vendor_internal_delta_errors else None,
        })
        if scenario_pnls:
            tail = historical_var_es(tuple(scenario_pnls), Decimal("0.95"))
            metrics.update({
                "worst_scenario_pnl": min(scenario_pnls),
                "scenario_var_95": tail.value_at_risk,
                "scenario_expected_shortfall_95": tail.expected_shortfall,
                "scenario_observations": tail.observation_count,
            })
        else:
            metrics.update({
                "worst_scenario_pnl": Decimal("0"), "scenario_var_95": Decimal("0"),
                "scenario_expected_shortfall_95": Decimal("0"), "scenario_observations": 0,
            })
        if validity:
            status = ResultStatus.INVALID if any(reason in {"unpriced_positions", "open_structures_at_end", "cash_reconciliation_error"} for reason in validity) else ResultStatus.PARTIAL
        else:
            status = ResultStatus.VALID
        return BacktestResult(
            run_id, self.strategy.strategy_id, self.dataset.manifest.dataset_id, self.config, status,
            tuple(dict.fromkeys(validity)), tuple(intents), tuple(decisions), tuple(orders.values()), tuple(fills),
            tuple(settlements), tuple(snapshots), tuple(equity), self.strategy.decisions, metrics,
        )

    def _validate_inputs(self) -> None:
        if self.config.start.tzinfo is None or self.config.end.tzinfo is None:
            raise ValueError("backtest range must be timezone-aware")
        if self.config.start >= self.config.end:
            raise ValueError("backtest start must precede end")
        manifest = self.dataset.manifest
        if min(manifest.contract_coverage, manifest.quote_coverage) < self.config.minimum_data_coverage:
            raise ValueError("dataset coverage is below configured threshold")
        if self.config.commission_per_contract <= 0:
            raise ValueError("commission must be non-zero")

    @staticmethod
    def _equity_point(snapshot):
        drawdown = snapshot.equity_liquidation / snapshot.peak_equity - 1 if snapshot.peak_equity else Decimal("0")
        return EquityPoint(
            snapshot.timestamp, snapshot.equity_mid, snapshot.equity_liquidation, snapshot.cash, drawdown,
            snapshot.delta, snapshot.gamma, snapshot.theta, snapshot.vega, not snapshot.unpriced_positions,
        )

    def _force_close(self, portfolio, market, commission, planner, ids, catalog):
        intents, fills, orders, reasons = [], [], [], []
        conservative = ListedOptionComboFillModel(FillModelType.CONSERVATIVE, commission, catalog, id_factory=ids.next)
        for structure in list(portfolio.structures.values()):
            legs = tuple(LegIntent(instrument_id, TradeSide.SELL if sign > 0 else TradeSide.BUY, abs(sign)) for instrument_id, sign in structure.legs)
            intent = CloseStructureIntent(
                structure.strategy_id, structure.structure_id, legs, structure.quantity, None, TimeInForce.IOC,
                "forced end-of-backtest liquidation", ids.next(),
            )
            intents.append(intent)
            order = planner.plan(intent, market.timestamp)
            order = replace(order, eligible_at=market.timestamp).transition(OrderStatus.WORKING)
            attempt = conservative.attempt(order, market)
            orders.append(attempt.order)
            if attempt.fill:
                portfolio.apply_fill(attempt.fill)
                fills.append(attempt.fill)
            else:
                reasons.append(f"force_close_failed:{structure.structure_id}:{attempt.reason}")
        return intents, fills, orders, reasons

    @staticmethod
    def _reconstruct_cash(initial_cash, fills, settlements, catalog):
        cash = initial_cash
        for fill in fills:
            for leg in fill.legs:
                definition = definition_at(catalog, leg.instrument_id, fill.timestamp)
                spec = contract_spec(definition)
                multiplier = option_multiplier(spec) if is_option_spec(spec) else getattr(spec, "contract_size", Decimal("1"))
                signed_quantity = leg.side.sign * leg.ratio * fill.quantity
                cash -= Decimal(signed_quantity) * leg.price * multiplier
            cash -= fill.commission
        for settlement in settlements:
            cash += settlement.cash_delta
        return cash

    @staticmethod
    def _scenario_pnls(portfolio, valuation, catalog, at):
        positions = []
        for instrument_id, position in portfolio.positions.items():
            if position.quantity == 0:
                continue
            item = valuation.get(instrument_id)
            if item is None or item.pricing is None:
                continue
            definition = definition_at(catalog, instrument_id, at)
            spec = contract_spec(definition)
            multiplier = option_multiplier(spec) if is_option_spec(spec) else getattr(spec, "contract_size", Decimal("1"))
            structure_id = next((str(structure.structure_id) for structure in portfolio.structures.values() if any(leg_id == instrument_id for leg_id, _ in structure.legs)), None)
            positions.append(RevaluationPosition(
                instrument_id, position.quantity, multiplier, item.inputs, item.model,
                structure_id, portfolio.account.value,
            ))
        if not positions:
            return ()
        engine = ScenarioEngine()
        return tuple(engine.evaluate(tuple(positions), scenario).pnl for scenario in standard_scenario_grid())
