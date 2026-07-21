from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

from kairospy.backtest.feed import MarketSnapshot
from kairospy.trading.product import ListedOptionSpec, OptionRight
from kairospy.pricing.option_valuation import ValuationSnapshot
from kairospy.reference import ReferenceCatalog
from kairospy.reference.access import contract_spec, definition_at

from .runtime import FactorQuality, FactorSnapshot, FactorSpec, implementation_hash
from kairospy.capture.features import FeatureEngine


@dataclass(frozen=True, slots=True)
class OptionSkewFactorConfig:
    target_put_delta: Decimal = Decimal("-0.25")
    minimum_rank_history: int = 252

    def __post_init__(self)->None:
        if not Decimal("-1")<self.target_put_delta<Decimal("0"):raise ValueError("target put delta must be in (-1,0)")
        if self.minimum_rank_history<0:raise ValueError("minimum rank history cannot be negative")


class OptionSkewFactorRuntime:
    """Point-in-time 25-delta put/ATM skew with expanding historical rank."""

    def __init__(self,catalog:ReferenceCatalog,config:OptionSkewFactorConfig=OptionSkewFactorConfig(),*,
                 input_identity:str,factor_id:str="spxw-put-skew",version:str="1.0.0")->None:
        self.catalog=catalog;self.config=config;self.input_identity=input_identity;self._history=[];self._snapshot=None
        self._spec=FactorSpec(factor_id,version,("market_snapshot.option_quotes","internal.option_valuation"),
            (("target_put_delta",str(config.target_put_delta)),("minimum_rank_history",str(config.minimum_rank_history))),
            max(1,config.minimum_rank_history),("put25_atm_skew","skew_rank","put25_iv","atm_iv"),
            "kairospy.features.option_skew:OptionSkewFactorRuntime",implementation_hash(Path(__file__)))

    @property
    def spec(self):return self._spec
    def snapshot(self):return self._snapshot

    def update_market(self,market:MarketSnapshot,valuation:ValuationSnapshot)->FactorSnapshot|None:
        candidates=[]
        for item in valuation.instruments:
            definition=definition_at(self.catalog,item.instrument_id,market.timestamp);spec=contract_spec(definition)
            if isinstance(spec,ListedOptionSpec) and spec.right is OptionRight.PUT and item.pricing is not None and item.implied_vol.volatility is not None:
                candidates.append((item,spec))
        if len(candidates)<2:return None
        expiries={spec.expiry for _,spec in candidates};expiry=min(expiries)
        candidates=[pair for pair in candidates if pair[1].expiry==expiry]
        if len(candidates)<2:return None
        underlying=candidates[0][1].underlying;spot=dict(market.reference_prices).get(underlying)
        if spot is None:return None
        put25,_=min(candidates,key=lambda pair:abs(pair[0].pricing.delta-self.config.target_put_delta))
        atm,_=min(candidates,key=lambda pair:abs(pair[1].strike-spot))
        put_iv=put25.implied_vol.volatility;atm_iv=atm.implied_vol.volatility
        assert put_iv is not None and atm_iv is not None
        skew=put_iv-atm_iv
        ready=len(self._history)>=self.config.minimum_rank_history
        rank=(Decimal("0.5") if not self._history else
              Decimal(sum(value<=skew for value in self._history))/Decimal(len(self._history))) if ready else None
        self._history.append(skew)
        quality=FactorQuality.READY if ready else FactorQuality.WARMING_UP
        state={"underlying":underlying.value,"as_of":market.timestamp.isoformat(),"history":[str(v) for v in self._history],
               "factor_spec_hash":self.spec.spec_hash}
        self._snapshot=FactorSnapshot(self.spec.factor_id,self.spec.version,self.spec.spec_hash,underlying,market.timestamp,
            (("put25_atm_skew",skew),("skew_rank",rank),("put25_iv",put_iv),("atm_iv",atm_iv)),
            len(self._history),quality,self.input_identity,sha256(json.dumps(state,sort_keys=True,separators=(",",":")).encode()).hexdigest())
        return self._snapshot

    def dump_state(self):return {"factor_spec_hash":self.spec.spec_hash,"input_identity":self.input_identity,
        "history":[str(value) for value in self._history]}
    def restore(self,state):
        if state.get("factor_spec_hash")!=self.spec.spec_hash or state.get("input_identity")!=self.input_identity:
            raise ValueError("option skew factor state identity mismatch")
        self._history=[Decimal(str(value)) for value in state.get("history",[])]
        self._snapshot=None


class OptionFearCoolingFactorRuntime:
    """Governed feature builder for IV percentile, put skew and IV cooling used by iron condors."""
    def __init__(self,catalog:ReferenceCatalog,*,input_identity:str,factor_id="option-fear-cooling",version="1.0.0"):
        self.catalog=catalog;self.input_identity=input_identity;self.engine=FeatureEngine();self._snapshot=None
        self._spec=FactorSpec(factor_id,version,("internal.option_valuation",),(('history','expanding'),),2,
            ("put_skew","iv_percentile","average_iv_change"),
            "kairospy.features.option_skew:OptionFearCoolingFactorRuntime",implementation_hash(Path(__file__)))
    @property
    def spec(self):return self._spec
    def snapshot(self):return self._snapshot
    def update_market(self,market:MarketSnapshot,valuation:ValuationSnapshot):
        feature=self.engine.update(valuation)
        underlying=valuation.surface.underlying_id if valuation.surface is not None else None
        if underlying is None:
            for item in valuation.instruments:
                spec=contract_spec(definition_at(self.catalog,item.instrument_id,market.timestamp))
                if isinstance(spec,ListedOptionSpec):underlying=spec.underlying;break
        if underlying is None:return None
        ready=all(value is not None for value in (feature.put_skew,feature.iv_percentile,feature.average_iv_change))
        values=(("put_skew",feature.put_skew),("iv_percentile",feature.iv_percentile),("average_iv_change",feature.average_iv_change))
        material={"factor_spec_hash":self.spec.spec_hash,"as_of":market.timestamp.isoformat(),"values":[(k,str(v)) for k,v in values]}
        self._snapshot=FactorSnapshot(self.spec.factor_id,self.spec.version,self.spec.spec_hash,underlying,market.timestamp,values,
            feature.observation_count,FactorQuality.READY if ready else FactorQuality.WARMING_UP,self.input_identity,
            sha256(json.dumps(material,sort_keys=True,separators=(",",":")).encode()).hexdigest())
        return self._snapshot
    def dump_state(self):return {"factor_spec_hash":self.spec.spec_hash,"input_identity":self.input_identity}
    def restore(self,state):
        if state!={"factor_spec_hash":self.spec.spec_hash,"input_identity":self.input_identity}:raise ValueError("fear cooling factor state mismatch")
