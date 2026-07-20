from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from kairos.backtest.execution import combo_quote
from kairos.domain.execution import TradeSide
from kairos.domain.intent import CloseStructureIntent, LegIntent, OpenStructureIntent
from kairos.domain.order import Fill, TimeInForce
from kairos.domain.product import OptionRight, ProductType, is_option_spec
from kairos.reference.access import contract_spec, definition_at
from kairos.strategies.strategy_protocols import StrategyContext, StrategyDecision
from kairos.domain.strategy_contract import StrategyLifecycle, StrategySpec


@dataclass(frozen=True, slots=True)
class BtcIronCondorConfig:
    target_dte_days: int = 14
    dte_tolerance_days: int = 3
    short_put_delta: Decimal = Decimal("-0.25")
    long_put_delta: Decimal = Decimal("-0.10")
    short_call_delta: Decimal = Decimal("0.25")
    long_call_delta: Decimal = Decimal("0.10")
    minimum_put_skew: Decimal = Decimal("0.065")
    minimum_iv_percentile: Decimal = Decimal("0.80")
    require_iv_cooling: bool = True
    holding_days: int = 7
    quantity: int = 1
    maximum_leg_spread: Decimal = Decimal("0.10")
    minimum_credit: Decimal = Decimal("0")
    risk_budget_fraction: Decimal = Decimal("0.02")
    execution_policy_id: str = "taker-combo-v1"
    signal_factor_id:str="option-fear-cooling"

    def __post_init__(self) -> None:
        if self.target_dte_days < 1 or self.dte_tolerance_days < 0 or self.holding_days < 1 or self.quantity < 1:
            raise ValueError("invalid iron-condor time or quantity configuration")
        if not Decimal("0") < self.risk_budget_fraction <= Decimal("1"):
            raise ValueError("risk budget fraction must be in (0, 1]")


class BtcIronCondorStrategy:
    """Frozen economic strategy model; venue execution remains outside the strategy."""

    def __init__(self, config: BtcIronCondorConfig = BtcIronCondorConfig(), *, study_spec_hash: str) -> None:
        self.config = config; self._study_spec_hash = study_spec_hash
        self._decisions: list[StrategyDecision] = []; self._evaluated_dates = set()

    @property
    def strategy_id(self) -> str: return "btc-iron-condor-v1"

    @property
    def decisions(self) -> tuple[StrategyDecision, ...]: return tuple(self._decisions)

    @property
    def strategy_spec(self) -> StrategySpec:
        c=self.config
        return StrategySpec(self.strategy_id,"1.2.0",StrategyLifecycle.DRAFT,
            (ProductType.CRYPTO_OPTION,), ("short_volatility","short_gamma","skew"),
            ("variance_risk_premium","skew_mean_reversion"),("gamma","vega","jump","liquidity"),
            (("underlying","BTC"),("target_dte_days",c.target_dte_days),("dte_tolerance_days",c.dte_tolerance_days)),
            (c.signal_factor_id,),
            (("minimum_put_skew",str(c.minimum_put_skew)),("minimum_iv_percentile",str(c.minimum_iv_percentile)),("require_iv_cooling",c.require_iv_cooling)),
            (("structure","iron_condor"),("short_put_delta",str(c.short_put_delta)),("long_put_delta",str(c.long_put_delta)),
             ("short_call_delta",str(c.short_call_delta)),("long_call_delta",str(c.long_call_delta))),
            ("signal_gate_passed",),(f"hold_{c.holding_days}_calendar_days",),("no_intraday_rebalance",),
            c.risk_budget_fraction,("point_in_time_option_universe","synchronous_quotes","quote_size"),
            ("combo_orders",c.execution_policy_id),self._study_spec_hash)

    def on_start(self, context: StrategyContext): return ()

    def on_market(self, context: StrategyContext):
        if context.working_orders: return self._record(context,"wait","working order exists")
        structures=[item for item in context.portfolio.open_structures if item.strategy_id==self.strategy_id]
        if structures: return self._close_if_due(context,structures[0])
        day=context.now.date()
        if day in self._evaluated_dates: return ()
        self._evaluated_dates.add(day)
        if not self._signal_ready(context): return self._record(context,"skip","fear-cooling signal not satisfied")
        return self._open(context)

    def _signal_ready(self, context):
        c=self.config
        try:
            factor=context.factor(c.signal_factor_id)
            put_skew=factor.get("put_skew");iv_percentile=factor.get("iv_percentile");average_iv_change=factor.get("average_iv_change")
        except LookupError:
            f=context.features
            put_skew=getattr(f,"put_skew",None);iv_percentile=getattr(f,"iv_percentile",None);average_iv_change=getattr(f,"average_iv_change",None)
        return bool(put_skew is not None and put_skew>=c.minimum_put_skew
            and iv_percentile is not None and iv_percentile>=c.minimum_iv_percentile
            and (not c.require_iv_cooling or average_iv_change is not None and average_iv_change<=0))

    def _open(self,context):
        candidates=[]
        for item in context.market.instruments:
            definition=definition_at(context.catalog,item.instrument_id,context.now);option=contract_spec(definition)
            if not is_option_spec(option): continue
            dte=(option.expiry.date()-context.now.date()).days
            delta=self._delta(context,item)
            if abs(dte-self.config.target_dte_days)<=self.config.dte_tolerance_days and delta is not None:
                candidates.append((item,option,delta))
        if not candidates: return self._record(context,"skip","no eligible option candidates")
        expiry=min({option.expiry for _,option,_ in candidates},key=lambda value:(abs((value.date()-context.now.date()).days-self.config.target_dte_days),value))
        same=[value for value in candidates if value[1].expiry==expiry]
        targets=((OptionRight.PUT,self.config.long_put_delta,TradeSide.BUY),
                 (OptionRight.PUT,self.config.short_put_delta,TradeSide.SELL),
                 (OptionRight.CALL,self.config.short_call_delta,TradeSide.SELL),
                 (OptionRight.CALL,self.config.long_call_delta,TradeSide.BUY))
        selected=[]
        for right,target,side in targets:
            values=[value for value in same if value[1].right is right]
            if not values: return self._record(context,"skip",f"missing {right.value} candidates")
            selected.append((min(values,key=lambda value:(abs(value[2]-target),value[0].instrument_id.value)),side))
        strikes=[value[0][1].strike for value in selected]
        if not strikes[0]<strikes[1]<strikes[2]<strikes[3]: return self._record(context,"skip","selected legs do not form an iron condor")
        legs=tuple(LegIntent(value[0].instrument_id,side) for value,side in selected)
        quote=combo_quote(legs,context.market,self.config.quantity)
        if quote is None or quote.max_spread>self.config.maximum_leg_spread or quote.natural<self.config.minimum_credit:
            return self._record(context,"skip","credit or liquidity gate failed")
        self._record(context,"open",f"expiry={expiry.isoformat()}, natural={quote.natural}")
        return (OpenStructureIntent(self.strategy_id,legs,self.config.quantity,self.config.minimum_credit,
            TimeInForce.DAY,"fear cooling iron condor",uuid5(NAMESPACE_URL,f"{self.strategy_id}:{context.now.isoformat()}:open")),)

    def _close_if_due(self,context,structure):
        if context.now < structure.opened_at+timedelta(days=self.config.holding_days): return ()
        legs=tuple(LegIntent(instrument_id,TradeSide.SELL if sign>0 else TradeSide.BUY,abs(sign)) for instrument_id,sign in structure.legs)
        self._record(context,"close","holding period elapsed")
        return (CloseStructureIntent(self.strategy_id,structure.structure_id,legs,structure.quantity,None,
            TimeInForce.DAY,"holding period elapsed",uuid5(NAMESPACE_URL,f"{self.strategy_id}:{context.now.isoformat()}:close")),)

    def on_fill(self,fill: Fill,context: StrategyContext):
        self._record(context,"fill",f"order={fill.order_id}");return ()
    def on_end(self,context): return ()

    def _record(self,context,action,reason):
        self._decisions.append(StrategyDecision(context.now.isoformat(),action,reason));return ()

    @staticmethod
    def _delta(context,item):
        if context.valuation is not None:
            value=context.valuation.get(item.instrument_id)
            if value is not None and value.pricing is not None:return value.pricing.delta
        return item.greeks.delta if item.greeks and item.greeks.delta is not None else None
