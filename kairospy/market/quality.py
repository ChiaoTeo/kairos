from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from kairospy.market.types import MarketQualityIssue, OptionMarketObservation


def validate_option_observation(
    observation: OptionMarketObservation,
    as_of: datetime,
    *,
    max_age_seconds: Decimal = Decimal("5"),
    max_relative_spread: Decimal = Decimal("1"),
) -> tuple[MarketQualityIssue, ...]:
    if as_of.tzinfo is None or observation.event_time.tzinfo is None:
        raise ValueError("market quality timestamps must be timezone-aware")
    issues = []
    bid, ask = observation.bid, observation.ask
    if bid is None or ask is None:
        issues.append(
            MarketQualityIssue("missing_two_sided_quote", "error", "bid and ask are required", observation.instrument_id)
        )
    else:
        if bid < 0 or ask < 0:
            issues.append(MarketQualityIssue("negative_quote", "error", "bid and ask cannot be negative", observation.instrument_id))
        if bid > ask:
            issues.append(MarketQualityIssue("crossed_quote", "error", "bid exceeds ask", observation.instrument_id))
        if bid <= ask and bid + ask > 0:
            mid = (bid + ask) / 2
            relative_spread = (ask - bid) / mid
            if relative_spread > max_relative_spread:
                issues.append(
                    MarketQualityIssue(
                        "wide_spread",
                        "warning",
                        f"relative spread {relative_spread} exceeds {max_relative_spread}",
                        observation.instrument_id,
                    )
                )
    age = Decimal(str((as_of - observation.event_time).total_seconds()))
    if age < 0:
        issues.append(MarketQualityIssue("future_event", "error", "event time is after snapshot as_of", observation.instrument_id))
    elif age > max_age_seconds:
        issues.append(MarketQualityIssue("stale_quote", "error", f"quote age {age}s exceeds {max_age_seconds}s", observation.instrument_id))
    return tuple(issues)


def blocking_issues(issues: tuple[MarketQualityIssue, ...]) -> tuple[MarketQualityIssue, ...]:
    return tuple(item for item in issues if item.severity == "error")
