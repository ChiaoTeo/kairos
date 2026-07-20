from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from kairos.domain.identity import InstrumentId
from kairos.domain.intent import TargetExposureIntent
from kairos.features import FactorQuality

from .strategy_protocols import StrategyContext, StrategyDecision


@dataclass(frozen=True, slots=True)
class SmaCrossStrategyConfig:
    instrument_id: InstrumentId
    factor_id: str = "sma-spread"
    long_fraction: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if not Decimal("0") < self.long_fraction <= Decimal("1"):
            raise ValueError("SMA long fraction must be in (0, 1]")


class SmaCrossStrategy:
    strategy_id = "sma-cross-v1"

    def __init__(self, config: SmaCrossStrategyConfig) -> None:
        self.config = config
        self._decisions: list[StrategyDecision] = []
        self._last_target: Decimal | None = None

    @property
    def decisions(self) -> tuple[StrategyDecision, ...]:
        return tuple(self._decisions)

    def on_start(self, context: StrategyContext):
        return ()

    def on_market(self, context: StrategyContext):
        factor = context.factor(self.config.factor_id)
        if factor.instrument_id != self.config.instrument_id:
            raise ValueError("SMA factor and strategy instrument differ")
        if factor.quality is not FactorQuality.READY:
            self._record(context, "warmup", f"observations={factor.observations}")
            return ()
        spread = factor.get("spread")
        if spread is None:
            self._record(context, "skip", "SMA spread unavailable")
            return ()
        target = self.config.long_fraction if spread > 0 else Decimal("0")
        if target == self._last_target:
            self._record(context, "hold", f"target_fraction={target}")
            return ()
        self._last_target = target
        action = "long" if target else "flat"
        self._record(context, action, f"sma_spread={spread}")
        intent_id = uuid5(
            NAMESPACE_URL,
            f"{self.strategy_id}:{context.now.isoformat()}:{self.config.instrument_id.value}:{target}",
        )
        return (TargetExposureIntent(
            intent_id, self.strategy_id, self.config.instrument_id, target,
            f"{action} from governed SMA factor",
        ),)

    def on_fill(self, fill, context: StrategyContext):
        return ()

    def on_end(self, context: StrategyContext):
        return ()

    def _record(self, context: StrategyContext, action: str, reason: str) -> None:
        self._decisions.append(StrategyDecision(
            context.now.isoformat(), action, reason, (self.config.instrument_id.value,),
        ))
