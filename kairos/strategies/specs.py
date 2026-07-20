from __future__ import annotations

from decimal import Decimal
from dataclasses import replace

from kairos.domain.capability import TimeInForce
from kairos.domain.product import ProductType
from kairos.domain.strategy_contract import StrategyLifecycle,StrategySpec
from kairos.execution.policy import ExecutionMode,ExecutionPolicy

from .bull_put_spread import BullPutSpreadConfig
from .cash_and_carry import CashAndCarryConfig
from .covered_call import CoveredCallConfig
from .protective_put import ProtectivePutConfig
from .sma_cross_study_backtest import SmaCrossConfig


def builtin_strategy_specs() -> tuple[tuple[StrategySpec,ExecutionPolicy],...]:
    bull=BullPutSpreadConfig();covered=CoveredCallConfig();protective=ProtectivePutConfig();carry=CashAndCarryConfig();sma=SmaCrossConfig()
    return (
        bull_put_strategy_spec(bull),
        (_spec("covered-call-v1",(ProductType.EQUITY,ProductType.LISTED_OPTION),("income","covered_option"),("option_premium",),("equity_downside","short_call"),
            (("equity_quantity",str(covered.equity_quantity)),("option_right","call")),("position",),(("shares_first",True),),
            (("equity_quantity",str(covered.equity_quantity)),("contracts",str(covered.contracts))),
            ("acquire_shares_before_short_call",),("external_exit_policy",),("position_change",),"taker-covered-v1",("multi_asset_orders",)),
         _policy("taker-covered-v1")),
        (_spec("protective-put-v1",(ProductType.EQUITY,ProductType.LISTED_OPTION),("tail_hedge",),("tail_protection",),("premium_decay","basis"),
            (("equity_quantity",str(protective.equity_quantity)),("option_right","put")),("position",),(("shares_first",True),),
            (("equity_quantity",str(protective.equity_quantity)),("contracts",str(protective.contracts))),
            ("acquire_shares_before_long_put",),("external_exit_policy",),("position_change",),"taker-protective-v1",("multi_asset_orders",)),
         _policy("taker-protective-v1")),
        (_spec("spot-perpetual-carry-v1",(ProductType.CRYPTO_SPOT,ProductType.PERPETUAL),("basis","carry"),("funding","basis_convergence"),("legging","liquidation","funding_reversal"),
            (("spot_quantity",str(carry.spot_quantity)),("maximum_leverage",str(carry.maximum_leverage))),("spot_perpetual_basis",),
            (("minimum_annualized_basis",str(carry.minimum_annualized_basis)),),(("spot_quantity",str(carry.spot_quantity)),("perpetual_hedge_ratio","-1")),
            ("basis_above_threshold",),("basis_or_risk_exit",),("basis_change",),"taker-carry-v1",("atomic_or_bounded_legging",)),
         _policy("taker-carry-v1")),
        sma_strategy_spec(sma),
    )


def register_builtin_strategies(root="data/strategies"):
    from hashlib import sha256
    from pathlib import Path
    from kairos.features import SmaFactorConfig,SmaFactorRuntime
    from .registry import StrategyImplementation,StrategyRegistry
    registry=StrategyRegistry(root)
    implementations={
        "bull-put-spread-v1":"bull_put_spread:BullPutSpreadStrategy",
        "covered-call-v1":"covered_call:CoveredCallStrategy",
        "protective-put-v1":"protective_put:ProtectivePutStrategy",
        "spot-perpetual-carry-v1":"cash_and_carry:CashAndCarryStrategy",
        "sma-cross-v1":"sma_cross_strategy:SmaCrossStrategy",
    }
    output=[]
    for spec,policy in builtin_strategy_specs():
        module,class_name=implementations[spec.strategy_id].split(":");source=Path(__file__).with_name(module+".py")
        implementation=StrategyImplementation(f"kairos.strategies.{module}:{class_name}",sha256(source.read_bytes()).hexdigest())
        factors=()
        if spec.strategy_id=="sma-cross-v1":
            factors=(SmaFactorRuntime(SmaFactorConfig(),input_identity="runtime-bound").spec,)
        output.append(registry.register(spec,policy,implementation=implementation,factor_specs=factors))
    return tuple(output)


def bull_put_strategy_spec(config: BullPutSpreadConfig) -> tuple[StrategySpec,ExecutionPolicy]:
    features=("delta","option_quotes") if config.signal_factor_id is None else ("delta","option_quotes",config.signal_factor_id)
    signal=(("target_short_delta",str(config.target_short_delta)),) if config.signal_factor_id is None else (
        ("target_short_delta",str(config.target_short_delta)),("minimum_skew_rank",str(config.minimum_skew_rank)))
    spec=_spec("bull-put-spread-v1",(ProductType.LISTED_OPTION,),("short_volatility","skew"),("theta","skew"),("gamma","jump"),
        (("rights","put"),("min_dte",config.min_dte),("max_dte",config.max_dte)),features,
        signal,(("structure","bull_put_spread"),("width",str(config.width)),("quantity",config.quantity)),
        (f"minimum_credit={config.min_credit}",),(f"profit_target={config.profit_target}",f"stop_loss_multiple={config.stop_loss_multiple}"),("none",),"taker-combo-v1",("combo_orders",))
    return (replace(spec,version="1.2.0") if config.signal_factor_id else spec,_policy("taker-combo-v1"))


def sma_strategy_spec(config: SmaCrossConfig) -> tuple[StrategySpec,ExecutionPolicy]:
    spec = _spec("sma-cross-v1",(ProductType.CRYPTO_SPOT,),("trend",),("direction","trend"),("whipsaw","gap"),
        (("long_only",True),),(f"sma_{config.fast_window}",f"sma_{config.slow_window}"),
        (("fast_window",config.fast_window),("slow_window",config.slow_window)),
        (("target_exposure","fully_invested_or_cash"),),("fast_above_slow",),("fast_below_slow",),("next_event",),
        "taker-spot-v1",("market_orders",))
    return (replace(spec, version="1.2.0"), _policy("taker-spot-v1",fee="configured_fee_bps"))


def _spec(strategy_id,products,archetypes,returns,risks,universe,features,signal,construction,entry,exit_,rebalance,policy_id,capabilities):
    return StrategySpec(strategy_id,"1.1.0",StrategyLifecycle.DRAFT,products,archetypes,returns,risks,universe,features,signal,
        construction,entry,exit_,rebalance,Decimal(".02"),("point_in_time_universe","synchronous_quotes"),(policy_id,*capabilities),
        "synthetic-mechanics-only")


def _policy(policy_id,fee="venue_versioned"):
    return ExecutionPolicy(policy_id,"1.0.0",ExecutionMode.TAKER,TimeInForce.IOC,Decimal("20"),order_latency_ms=250,
        slippage_model="top_of_book",fee_schedule=fee)
