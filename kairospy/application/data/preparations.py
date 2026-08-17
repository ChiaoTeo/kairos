"""Deterministic preparation of fine-grained option Market requirements."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .models import DataRequirement


@dataclass(frozen=True, slots=True)
class OptionMarketDataTarget:
    underlying: str
    provider_symbol: str
    instrument_id: str
    network_id: str | None
    start_time_unix_nanos: int
    end_time_unix_nanos: int

    def __post_init__(self) -> None:
        for name in ("underlying", "provider_symbol", "instrument_id"):
            value = str(getattr(self, name)).strip()
            if not value:
                raise ValueError(f"option Market data target {name} is required")
            object.__setattr__(self, name, value)
        if self.network_id is not None:
            network_id = self.network_id.strip()
            if not network_id:
                raise ValueError("option Market data target network_id cannot be empty")
            object.__setattr__(self, "network_id", network_id)
        if self.start_time_unix_nanos > self.end_time_unix_nanos:
            raise ValueError("option Market data target start cannot be after end")

    @classmethod
    def from_reference_event(
        cls,
        event: Mapping[str, Any],
        *,
        start_time_unix_nanos: int,
        end_time_unix_nanos: int,
    ) -> "OptionMarketDataTarget":
        if event.get("kind") != "option-contract":
            raise ValueError("option Market target requires an option-contract fact")
        return cls(
            underlying=str(event.get("underlying") or ""),
            provider_symbol=str(event.get("provider_symbol") or ""),
            instrument_id=str(event.get("instrument_id") or ""),
            network_id=(
                None if event.get("network_id") is None else str(event["network_id"])
            ),
            start_time_unix_nanos=start_time_unix_nanos,
            end_time_unix_nanos=end_time_unix_nanos,
        )


@dataclass(frozen=True, slots=True)
class OptionMarketPreparationApplication:
    """Build atomic per-contract Requirements for the shared acquisition path."""

    def requirements(
        self,
        targets: Iterable[OptionMarketDataTarget],
        *,
        kinds: tuple[str, ...] = ("quote",),
        credential_id: str = "massive-readonly",
        endpoint: str | None = None,
    ) -> tuple[DataRequirement, ...]:
        normalized_kinds = tuple(dict.fromkeys(kind.strip().lower() for kind in kinds))
        if not normalized_kinds or any(
            kind not in {"quote", "trade", "bar"} for kind in normalized_kinds
        ):
            raise ValueError(
                "option Market preparation kinds must be quote, trade or bar"
            )
        if not credential_id.strip():
            raise ValueError("option Market preparation credential_id is required")
        ordered_targets = sorted(
            tuple(targets),
            key=lambda item: (
                item.underlying,
                item.provider_symbol,
                item.start_time_unix_nanos,
                item.end_time_unix_nanos,
                item.instrument_id,
            ),
        )
        if not ordered_targets:
            raise ValueError("option Market preparation requires at least one target")

        result: list[DataRequirement] = []
        seen: set[tuple[str, str, int, int]] = set()
        for target in ordered_targets:
            for kind in normalized_kinds:
                identity = (
                    kind,
                    target.provider_symbol,
                    target.start_time_unix_nanos,
                    target.end_time_unix_nanos,
                )
                if identity in seen:
                    continue
                seen.add(identity)
                parameters = {
                    "symbol": target.provider_symbol,
                    "instrument_id": target.instrument_id,
                    "credential_id": credential_id,
                }
                if target.network_id is not None:
                    parameters["network_id"] = target.network_id
                if endpoint is not None:
                    parameters["endpoint"] = endpoint
                result.append(
                    DataRequirement(
                        owner="market",
                        kind=kind,
                        subject=(
                            f"{target.underlying.upper()}-options/"
                            f"{target.provider_symbol}"
                        ),
                        start_time_unix_nanos=target.start_time_unix_nanos,
                        end_time_unix_nanos=target.end_time_unix_nanos,
                        product="options",
                        source="massive",
                        minimum_quality="validated",
                        parameters=parameters,
                    )
                )
        return tuple(result)


__all__ = ["OptionMarketDataTarget", "OptionMarketPreparationApplication"]
