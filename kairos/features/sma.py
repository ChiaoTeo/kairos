from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from kairos.contracts import CanonicalEventEnvelope
from kairos.domain.identity import InstrumentId
from kairos.domain.market_data import Bar

from .runtime import (
    CanonicalBarFactorRuntime, FactorQuality, FactorSnapshot, FactorSpec, implementation_hash,
)


@dataclass(frozen=True, slots=True)
class SmaFactorConfig:
    fast_window: int = 20
    slow_window: int = 50

    def __post_init__(self) -> None:
        if self.fast_window < 1 or self.slow_window <= self.fast_window:
            raise ValueError("SMA windows must satisfy 1 <= fast_window < slow_window")


class SmaFactorRuntime(CanonicalBarFactorRuntime):
    def __init__(self, config: SmaFactorConfig = SmaFactorConfig(), *, input_identity: str,
                 factor_id: str = "sma-spread", version: str = "1.0.0") -> None:
        super().__init__()
        if not input_identity.strip():
            raise ValueError("factor input identity is required")
        self.config = config
        self.input_identity = input_identity
        self._closes: deque[Decimal] = deque(maxlen=config.slow_window)
        self._instrument_id: InstrumentId | None = None
        self._snapshot: FactorSnapshot | None = None
        source = Path(__file__)
        self._spec = FactorSpec(
            factor_id, version, ("canonical.bar.close",),
            (("fast_window", str(config.fast_window)), ("slow_window", str(config.slow_window))),
            config.slow_window, ("fast_sma", "slow_sma", "spread"),
            "kairos.features.sma:SmaFactorRuntime", implementation_hash(source),
        )

    @property
    def spec(self) -> FactorSpec:
        return self._spec

    def update(self, event: CanonicalEventEnvelope) -> FactorSnapshot | None:
        bar = self._bar(event)
        return self.update_bar(bar) if bar is not None else None

    def update_bar(self, bar: Bar) -> FactorSnapshot:
        if self._instrument_id is not None and bar.instrument_id != self._instrument_id:
            raise ValueError("one SMA factor runtime can process only one instrument")
        self._instrument_id = bar.instrument_id
        self._closes.append(bar.close)
        fast = _mean(tuple(self._closes)[-self.config.fast_window:]) if len(self._closes) >= self.config.fast_window else None
        slow = _mean(tuple(self._closes)) if len(self._closes) >= self.config.slow_window else None
        spread = fast - slow if fast is not None and slow is not None else None
        quality = FactorQuality.READY if slow is not None else FactorQuality.WARMING_UP
        state_hash = _state_hash(bar.instrument_id, tuple(self._closes), bar.end, self.spec.spec_hash)
        self._snapshot = FactorSnapshot(
            self.spec.factor_id, self.spec.version, self.spec.spec_hash, bar.instrument_id, bar.end,
            (("fast_sma", fast), ("slow_sma", slow), ("spread", spread)),
            len(self._closes), quality, self.input_identity, state_hash,
        )
        return self._snapshot

    def snapshot(self) -> FactorSnapshot | None:
        return self._snapshot

    def dump_state(self) -> dict[str, object]:
        return {
            "factor_spec_hash": self.spec.spec_hash,
            "input_identity": self.input_identity,
            "instrument_id": self._instrument_id.value if self._instrument_id else None,
            "closes": [str(value) for value in self._closes],
            "snapshot_as_of": self._snapshot.as_of.isoformat() if self._snapshot else None,
        }

    def restore(self, state: dict[str, object]) -> None:
        if state.get("factor_spec_hash") != self.spec.spec_hash:
            raise ValueError("factor state belongs to a different FactorSpec")
        if state.get("input_identity") != self.input_identity:
            raise ValueError("factor state belongs to a different input")
        instrument = state.get("instrument_id")
        closes = state.get("closes")
        if not isinstance(closes, list) or len(closes) > self.config.slow_window:
            raise ValueError("invalid SMA factor state")
        self._instrument_id = InstrumentId(str(instrument)) if instrument else None
        self._closes.clear()
        self._closes.extend(Decimal(str(value)) for value in closes)
        self._snapshot = None


def batch_sma_factors(
    bars: tuple[Bar, ...], config: SmaFactorConfig = SmaFactorConfig(), *, input_identity: str,
) -> tuple[FactorSnapshot, ...]:
    runtime = SmaFactorRuntime(config, input_identity=input_identity)
    return tuple(runtime.update_bar(bar) for bar in bars)


def _mean(values: tuple[Decimal, ...]) -> Decimal:
    return sum(values, Decimal("0")) / Decimal(len(values))


def _state_hash(instrument: InstrumentId, closes: tuple[Decimal, ...], as_of, spec_hash: str) -> str:
    from hashlib import sha256
    import json

    material = {
        "instrument_id": instrument.value,
        "closes": [str(value) for value in closes],
        "as_of": as_of.isoformat(),
        "factor_spec_hash": spec_hash,
    }
    return sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
