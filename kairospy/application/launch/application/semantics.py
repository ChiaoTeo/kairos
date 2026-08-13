"""Explicitly supported option semantics for the first backtest slice."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class OptionBacktestConstraints:
    hold_through_expiry: bool = False
    assignment_enabled: bool = False
    exercise_enabled: bool = False
    naked_options_enabled: bool = False
    zero_dte_enabled: bool = False
    dynamic_delta_hedging_enabled: bool = False
    avoid_short_dividend_window: bool = True
    package_fill_required: bool = True
    maximum_open_spreads: int = 1
    exit_before_expiry_days: int = 1
    dividend_buffer_days: int = 1

    def __post_init__(self) -> None:
        unsupported = {
            "hold_through_expiry": self.hold_through_expiry,
            "assignment_enabled": self.assignment_enabled,
            "exercise_enabled": self.exercise_enabled,
            "naked_options_enabled": self.naked_options_enabled,
            "zero_dte_enabled": self.zero_dte_enabled,
            "dynamic_delta_hedging_enabled": self.dynamic_delta_hedging_enabled,
        }
        enabled = sorted(name for name, value in unsupported.items() if value)
        if enabled:
            raise ValueError(
                "first option backtest slice does not support: " + ", ".join(enabled)
            )
        if not self.avoid_short_dividend_window:
            raise ValueError("first option backtest slice must avoid dividend windows")
        if not self.package_fill_required:
            raise ValueError("first option backtest slice requires package fills")
        if self.maximum_open_spreads != 1:
            raise ValueError(
                "first option backtest slice allows exactly one open spread"
            )
        if self.exit_before_expiry_days < 1:
            raise ValueError("option backtest must exit at least one day before expiry")
        if self.dividend_buffer_days < 1:
            raise ValueError("option backtest dividend buffer must be positive")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def limitations(self) -> tuple[str, ...]:
        return (
            "positions exit before expiry; assignment and exercise are unsupported",
            "short Put positions cannot cross the configured dividend window",
            "at most one protected spread can be open",
            "fills use deterministic all-or-nothing package semantics",
            "naked options, 0DTE and dynamic Delta hedging are unsupported",
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "OptionBacktestConstraints":
        unknown = sorted(set(value) - set(cls.__dataclass_fields__))
        if unknown:
            raise ValueError(
                "unknown option backtest constraints: " + ", ".join(unknown)
            )
        return cls(**dict(value))


__all__ = ["OptionBacktestConstraints"]
