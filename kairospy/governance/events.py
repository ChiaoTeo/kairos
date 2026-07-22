from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from kairospy.identity import InstrumentId


@dataclass(frozen=True, slots=True)
class DataWarningRaised:
    code: str
    message: str
    instrument_id: InstrumentId | None = None


GovernancePayload: TypeAlias = DataWarningRaised
