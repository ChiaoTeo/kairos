"""Cross-module backtest orchestration.

Execution owns simulated orders and fills. Account owns settlement and
mark-to-market. This facade only sequences the two application boundaries.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from kairospy.infrastructure.contracts.account import AccountContractClient
from kairospy.infrastructure.contracts.execution import backtest_run


def run_backtest(
    execution_socket: str,
    account: AccountContractClient,
    request: Mapping[str, Any],
    *,
    segment_key: str = "spot",
    quote_asset: str = "USDT",
) -> dict[str, Any]:
    """Run simulated execution, settle fills, and mark the final market state.

    The request is intentionally transport-shaped at this boundary so the
    launch layer can preserve the versioned Execution contract. It must
    contain orders and market_events for a simulation run.
    """
    result = backtest_run(execution_socket, dict(request))
    fills = sorted(
        result.get("fills", []),
        key=lambda fill: int(fill["occurred_at_unix_nanos"]),
    )
    market_events = sorted(
        request.get("market_events", []),
        key=lambda event: int(
            (_quote_payload(event) or {}).get("observed_at_unix_nanos", 0)
        ),
    )
    applied_fills = 0
    fill_index = 0
    equity_curve: list[dict[str, Any]] = []

    def apply_fill(fill: Mapping[str, Any]) -> None:
        nonlocal applied_fills
        quantity = Decimal(str(fill["quantity"]))
        price = Decimal(str(fill["price"]))
        fee = Decimal(str(fill.get("fee", "0")))
        notional = quantity * price
        settlement_delta = notional if fill["side"] == "Sell" else -notional
        account.apply_simulated_fill(
            {
                "fill_id": fill["fill_id"],
                "order_id": fill["order_id"],
                "segment_key": segment_key,
                "instrument_id": fill["instrument_id"],
                "quantity": _decimal_wire(quantity),
                "price": _decimal_wire(price),
                "side": fill["side"],
                "settlement_asset": quote_asset,
                "settlement_delta": _decimal_wire(settlement_delta),
                "fee_asset": quote_asset,
                "fee_amount": _decimal_wire(fee),
                "occurred_at_unix_nanos": fill["occurred_at_unix_nanos"],
            }
        )
        applied_fills += 1

    for event in market_events:
        payload = _quote_payload(event)
        if payload is None:
            continue
        event_time = int(payload["observed_at_unix_nanos"])
        while (
            fill_index < len(fills)
            and int(fills[fill_index]["occurred_at_unix_nanos"]) <= event_time
        ):
            apply_fill(fills[fill_index])
            fill_index += 1
        mark = _quote_mark(payload)
        if mark is None:
            continue
        try:
            account.mark_to_market(
                {
                    "segment_key": segment_key,
                    "instrument_id": payload["instrument_id"],
                    "quote_asset": quote_asset,
                    "mark_price": _decimal_wire(mark),
                    "observed_at_unix_nanos": payload["observed_at_unix_nanos"],
                }
            )
        except RuntimeError:
            # A quote can arrive before the first position exists. The next
            # mark after a fill is authoritative and will be applied.
            continue
        try:
            snapshot = account.snapshot()
        except (AttributeError, RuntimeError):
            snapshot = None
        equity_curve.append(
            {
                "observed_at_unix_nanos": event_time,
                "snapshot": snapshot,
            }
        )

    while fill_index < len(fills):
        apply_fill(fills[fill_index])
        fill_index += 1

    try:
        final_snapshot = account.snapshot()
    except (AttributeError, RuntimeError):
        final_snapshot = None

    return {
        "execution": result,
        "applied_fills": applied_fills,
        "equity_curve": equity_curve,
        "final_account": final_snapshot,
    }


def _quote_mark(event: Mapping[str, Any]) -> Decimal | None:
    bid = event.get("bid_price")
    ask = event.get("ask_price")
    if bid is not None and ask is not None:
        return (Decimal(str(bid)) + Decimal(str(ask))) / Decimal("2")
    if bid is not None:
        return Decimal(str(bid))
    if ask is not None:
        return Decimal(str(ask))
    return None


def _quote_payload(event: Mapping[str, Any]) -> Mapping[str, Any] | None:
    if "Quote" in event and isinstance(event["Quote"], Mapping):
        return event["Quote"]
    if "quote" in event and isinstance(event["quote"], Mapping):
        return event["quote"]
    if "Bar" in event and isinstance(event["Bar"], Mapping):
        bar = event["Bar"]
        close = bar.get("close")
        if close is not None:
            return {
                "instrument_id": bar.get("instrument_id"),
                "bid_price": close,
                "ask_price": close,
                "observed_at_unix_nanos": bar.get("observed_at_unix_nanos"),
            }
    if "bar" in event and isinstance(event["bar"], Mapping):
        bar = event["bar"]
        close = bar.get("close")
        if close is not None:
            return {
                "instrument_id": bar.get("instrument_id"),
                "bid_price": close,
                "ask_price": close,
                "observed_at_unix_nanos": bar.get("observed_at_unix_nanos"),
            }
    if "instrument_id" in event and ("bid_price" in event or "ask_price" in event):
        return event
    return None


def _decimal_wire(value: Decimal) -> dict[str, int]:
    normalized = value.normalize()
    sign, digits, exponent = normalized.as_tuple()
    if not isinstance(exponent, int):
        raise ValueError("decimal wire value must be finite")
    mantissa = int("".join(str(digit) for digit in digits) or "0")
    if sign:
        mantissa = -mantissa
    if exponent >= 0:
        mantissa *= 10**exponent
        scale = 0
    else:
        scale = -exponent
    return {"mantissa": mantissa, "scale": scale}
