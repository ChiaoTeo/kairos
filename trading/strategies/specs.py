from __future__ import annotations

from decimal import Decimal

from trading.domain.capability import TimeInForce
from trading.domain.product import ProductType
from trading.domain.strategy_contract import StrategyLifecycle,StrategySpec
from trading.execution.policy import ExecutionMode,ExecutionPolicy

from .bull_put_spread import BullPutSpreadConfig
from .cash_and_carry import CashAndCarryConfig
from .covered_call import CoveredCallConfig
from .protective_put import ProtectivePutConfig
from .sma_cross import SmaCrossConfig


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
        (_spec("sma-cross-v1",(ProductType.CRYPTO_SPOT,),("trend",),("direction","trend"),("whipsaw","gap"),
            (("long_only",True),),(f"sma_{sma.fast_window}",f"sma_{sma.slow_window}"),(("fast_window",sma.fast_window),("slow_window",sma.slow_window)),
            (("target_position","fully_invested_or_cash"),),("fast_above_slow",),("fast_below_slow",),("next_bar",),"taker-spot-v1",("market_orders",)),
         _policy("taker-spot-v1",fee="configured_fee_bps")),
    )


def register_builtin_strategies(root="data/strategies"):
    from .registry import StrategyRegistry
    registry=StrategyRegistry(root)
    return tuple(registry.register(spec,policy) for spec,policy in builtin_strategy_specs())


def bull_put_strategy_spec(config: BullPutSpreadConfig) -> tuple[StrategySpec,ExecutionPolicy]:
    return (_spec("bull-put-spread-v1",(ProductType.LISTED_OPTION,),("short_volatility","skew"),("theta","skew"),("gamma","jump"),
        (("rights","put"),("min_dte",config.min_dte),("max_dte",config.max_dte)),("delta","option_quotes"),
        (("target_short_delta",str(config.target_short_delta)),),(("structure","bull_put_spread"),("width",str(config.width)),("quantity",config.quantity)),
        (f"minimum_credit={config.min_credit}",),(f"profit_target={config.profit_target}",f"stop_loss_multiple={config.stop_loss_multiple}"),("none",),"taker-combo-v1",("combo_orders",)),
        _policy("taker-combo-v1"))


def _spec(strategy_id,products,archetypes,returns,risks,universe,features,signal,construction,entry,exit_,rebalance,policy_id,capabilities):
    return StrategySpec(strategy_id,"1.1.0",StrategyLifecycle.DRAFT,products,archetypes,returns,risks,universe,features,signal,
        construction,entry,exit_,rebalance,Decimal(".02"),("point_in_time_universe","synchronous_quotes"),(policy_id,*capabilities),
        "synthetic-mechanics-only")


def _policy(policy_id,fee="venue_versioned"):
    return ExecutionPolicy(policy_id,"1.0.0",ExecutionMode.TAKER,TimeInForce.IOC,Decimal("20"),order_latency_ms=250,
        slippage_model="top_of_book",fee_schedule=fee)
