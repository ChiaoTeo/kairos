from __future__ import annotations

import argparse
from decimal import Decimal
import json

from kairospy.analytics.features import BtcDeribitTradeSkewFeatureBuilder, BtcIvRvFeatureBuilder, BtcTermSkewFeatureBuilder
from kairospy.analytics.features.us_equity_momentum import UsEquityMomentumDatasetBuilder, UsEquityMomentumPolicy
from kairospy.analytics.pricing import PricingInput, PricingModel, OptionValuationService, implied_volatility, price_with_volatility
from kairospy.data import DatasetClient
from kairospy.data.contracts import RunMode
from kairospy.identity import InstrumentId
from kairospy.reference.contracts import OptionRight
from kairospy.risk import RevaluationPosition, Scenario, ScenarioEngine, explain_scenario


def features_command(args: argparse.Namespace) -> int:
    if args.feature_set == "us-equity-momentum-v1":
        if not args.source_directory or not args.dataset_id:
            raise SystemExit("us-equity-momentum-v1 requires --source-directory and --dataset-id")
        policy = UsEquityMomentumPolicy(
            minimum_price=args.minimum_price,
            minimum_adv20=args.minimum_adv20,
            minimum_history=args.minimum_history,
        )
        manifest = UsEquityMomentumDatasetBuilder(args.lake_root).build_from_ohlcv_directory(
            args.source_directory, dataset_id=args.dataset_id, policy=policy,
            corporate_actions_directory=args.corporate_actions_directory,
            reference_directory=args.reference_directory,
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    builders = {"btc-iv-rv-v1": BtcIvRvFeatureBuilder, "btc-term-skew-v1": BtcTermSkewFeatureBuilder,
                "btc-deribit-trade-skew-v1": BtcDeribitTradeSkewFeatureBuilder}
    release = builders[args.feature_set](args.lake_root).build()
    print(f"{release.release_id}: product={release.product_key} hash={release.content_hash}")
    return 0


def pricing_command(args: argparse.Namespace) -> int:
    model = PricingModel(args.model)
    if model is PricingModel.BLACK_76 and args.dividend_yield != 0:
        raise SystemExit("Black-76 requires --dividend-yield 0")
    if args.volatility is None and args.market_price is None:
        raise SystemExit("provide --volatility or --market-price")
    initial_vol = args.volatility if args.volatility is not None else Decimal("0.20")
    inputs = PricingInput(
        args.underlying, args.strike, args.years, args.rate, initial_vol,
        OptionRight(args.right), args.dividend_yield,
    )
    if args.market_price is not None:
        solved = implied_volatility(args.market_price, inputs, model)
        print(f"Solver: {solved.status.value}")
        if solved.volatility is None:
            print(f"Bounds: {solved.lower_price_bound} to {solved.upper_price_bound}")
            return 2
        inputs = PricingInput(
            inputs.underlying, inputs.strike, inputs.time_to_expiry, inputs.risk_free_rate,
            solved.volatility, inputs.right, inputs.dividend_yield,
        )
        print(f"Implied volatility: {solved.volatility}")
    result = price_with_volatility(inputs, inputs.volatility, model)
    print(f"Model: {result.model.value}")
    print(f"Price: {result.price}")
    print(f"Delta: {result.delta}")
    print(f"Gamma: {result.gamma}")
    print(f"Theta/year: {result.theta}")
    print(f"Vega: {result.vega}")
    print(f"Rho: {result.rho}")
    return 0


def vol_command(args: argparse.Namespace) -> int:
    client = DatasetClient(args.lake_root, run_mode=RunMode.WORKSPACE)
    feed = client.replay_snapshots(args.dataset)
    dataset = feed.dataset
    catalog = dataset.reference_catalog()
    valuation_engine = OptionValuationService(catalog, risk_free_rate=args.rate, dividend_yield=args.dividend_yield)
    surfaces, failures = [], []
    for market in dataset.slices:
        _, valuation = valuation_engine.value(market)
        failures.extend(valuation.failures)
        if valuation.surface is not None:
            surfaces.append(valuation.surface)
    calibrated = sum(any(smile.parameters is not None for smile in item.smiles) for item in surfaces)
    arbitrage_passed = sum(item.diagnostics.passed for item in surfaces)
    print(f"Dataset: {dataset.manifest.dataset_id}")
    print(f"Surfaces: {len(surfaces)}")
    print(f"Calibrated: {calibrated}")
    print(f"Arbitrage checks passed: {arbitrage_passed}")
    print(f"Valuation failures: {len(failures)}")
    if surfaces:
        from kairospy.surface.data_features import SurfaceFeaturePublisher
        release = SurfaceFeaturePublisher(args.lake_root).publish(
            tuple(surfaces), input_release_id=feed.release.release_id,
        )
        print(f"Last surface: {surfaces[-1].surface_id}")
        print(f"Last input hash: {surfaces[-1].input_hash}")
        print(f"Feature Release: {release.release_id}")
    return 0 if surfaces else 2


def risk_command(args: argparse.Namespace) -> int:
    model = PricingModel(args.model)
    if model is PricingModel.BLACK_76 and args.dividend_yield != 0:
        raise SystemExit("Black-76 requires --dividend-yield 0")
    position = RevaluationPosition(
        InstrumentId(args.instrument), args.quantity, args.multiplier,
        PricingInput(
            args.underlying, args.strike, args.years, args.rate, args.volatility,
            OptionRight(args.right), args.dividend_yield,
        ),
        model,
    )
    scenario = Scenario(
        "cli", args.spot_shock, args.vol_shock, args.skew_twist, args.term_twist,
        args.rate_shock, args.time_advance_days,
    )
    result = ScenarioEngine().evaluate((position,), scenario)
    explain = explain_scenario(position, scenario, result)
    print(f"Base value: {result.base_value}")
    print(f"Scenario value: {result.scenario_value}")
    print(f"PnL: {result.pnl}")
    print(f"Delta PnL: {explain.delta}")
    print(f"Gamma PnL: {explain.gamma}")
    print(f"Theta PnL: {explain.theta}")
    print(f"Vega PnL: {explain.vega}")
    print(f"Rho PnL: {explain.rho}")
    print(f"Residual: {explain.residual}")
    return 0
