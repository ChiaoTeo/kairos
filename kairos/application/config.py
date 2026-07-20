from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from kairos.ports import Environment


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    root: Path
    instrument_catalog: Path
    dataset_lake: Path
    runtime_database: Path
    artifacts: Path
    reference_catalog: Path | None = None

    @classmethod
    def under(cls, root: str | Path) -> "RuntimePaths":
        base = Path(root)
        return cls(
            base,
            base / "catalog" / "instruments.json",
            base,
            base / "runtime" / "runtime.sqlite3",
            base / "artifacts",
            base / "reference" / "catalog.json",
        )

    def validate(self) -> None:
        values = (self.root, self.instrument_catalog, self.dataset_lake, self.runtime_database, self.artifacts, self.reference_catalog or self.instrument_catalog)
        if any(not str(value) for value in values):
            raise ValueError("runtime paths cannot be empty")
        if not self.runtime_database.is_relative_to(self.root):
            raise ValueError("runtime database must be inside the configured root")
        if not self.artifacts.is_relative_to(self.root):
            raise ValueError("artifacts directory must be inside the configured root")


@dataclass(frozen=True, slots=True)
class ApplicationConfig:
    environment: Environment
    paths: RuntimePaths = field(default_factory=lambda: RuntimePaths.under("data"))
    maximum_clock_skew_ms: int = 1000
    reconciliation_tolerance: Decimal = Decimal("0.00000001")
    market_data_maximum_age_seconds: int = 30
    account_lock_lease_seconds: int = 30

    def validate(self) -> None:
        self.paths.validate()
        if self.maximum_clock_skew_ms < 0:
            raise ValueError("maximum clock skew cannot be negative")
        if self.reconciliation_tolerance < 0:
            raise ValueError("reconciliation tolerance cannot be negative")
        if self.market_data_maximum_age_seconds <= 0:
            raise ValueError("market data maximum age must be positive")
        if self.account_lock_lease_seconds <= 0:
            raise ValueError("account lock lease must be positive")
