from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from math import sqrt
from statistics import mean, pstdev

from trading.backtest.portfolio import PortfolioSnapshot
from trading.reference import ReferenceCatalog
from trading.reference.access import contract_spec, definition_at
from trading.domain.order import Fill, Order, OrderStatus, Settlement
from trading.domain.product import CryptoOptionSpec,ListedOptionSpec

from .result import EquityPoint
from .feed import MarketSlice


def calculate_metrics(
    equity: tuple[EquityPoint, ...],
    fills: tuple[Fill, ...],
    orders: tuple[Order, ...],
    rejected_count: int,
    portfolio_snapshots: tuple[PortfolioSnapshot, ...],
    market_slices: tuple[MarketSlice, ...] = (),
    settlements: tuple[Settlement, ...] = (),
    catalog: ReferenceCatalog | None = None,
) -> dict[str, object]:
    if not equity:
        return {"valid": False, "reason": "no equity points"}
    initial = equity[0].equity_liquidation
    final = equity[-1].equity_liquidation
    total_return = final / initial - 1 if initial else Decimal("0")
    duration_days = max(Decimal("1"), Decimal(str((equity[-1].timestamp - equity[0].timestamp).total_seconds() / 86400)))
    annual_return = (Decimal("1") + total_return) ** (Decimal("365") / duration_days) - 1 if total_return > Decimal("-1") else Decimal("-1")
    max_drawdown = min((point.drawdown for point in equity), default=Decimal("0"))
    returns = []
    for first, second in zip(equity, equity[1:]):
        if first.equity_liquidation:
            returns.append(float(second.equity_liquidation / first.equity_liquidation - 1))
    average = mean(returns) if returns else 0.0
    deviation = pstdev(returns) if len(returns) > 1 else 0.0
    downside = [value for value in returns if value < 0]
    downside_dev = sqrt(mean([value * value for value in downside])) if downside else 0.0
    sharpe = average / deviation * sqrt(252) if deviation else 0.0
    sortino = average / downside_dev * sqrt(252) if downside_dev else 0.0
    calmar = float(annual_return / abs(max_drawdown)) if max_drawdown else 0.0
    by_structure = defaultdict(Decimal)
    commissions = Decimal("0")
    slippage = Decimal("0")
    for fill in fills:
        multiplier = _multiplier(catalog, fill.legs[0].instrument_id, fill.timestamp)
        by_structure[str(fill.structure_id)] += fill.net_price * fill.quantity * multiplier - fill.commission
        commissions += fill.commission
        slippage += fill.slippage
    for settlement in settlements:
        by_structure[str(settlement.structure_id)] += settlement.cash_delta
    completed_ids = {str(fill.structure_id) for fill in fills if fill.is_closing} | {str(item.structure_id) for item in settlements}
    completed = [value for structure_id, value in by_structure.items() if structure_id in completed_ids]
    wins = [value for value in completed if value > 0]
    losses = [value for value in completed if value < 0]
    gross_profit = sum(wins, Decimal("0"))
    gross_loss = abs(sum(losses, Decimal("0")))
    valid_fills = len(fills)
    terminal_orders = [order for order in orders if order.status.terminal]
    grouped_year = _group_returns(equity, lambda point: str(point.timestamp.year))
    grouped_month = _group_returns(equity, lambda point: point.timestamp.strftime("%Y-%m"))
    grouped_dte, grouped_hour, grouped_iv = _trade_groups(fills, settlements, completed_ids, market_slices, catalog)
    max_greeks = {}
    for name in ("delta", "gamma", "theta", "vega"):
        values = [abs(getattr(point, name)) for point in equity if getattr(point, name) is not None]
        max_greeks[f"max_abs_{name}"] = max(values, default=Decimal("0"))
    daily = _group_returns(equity, lambda point: point.timestamp.date().isoformat())
    drawdown_duration = _max_drawdown_duration(equity)
    return {
        "valid": all(point.priced for point in equity),
        "initial_equity": initial,
        "final_equity": final,
        "total_return": total_return,
        "annualized_return": annual_return,
        "max_drawdown": max_drawdown,
        "max_drawdown_duration_seconds": drawdown_duration,
        "sharpe": Decimal(str(sharpe)),
        "sortino": Decimal(str(sortino)),
        "calmar": Decimal(str(calmar)),
        "orders": len(orders),
        "fills": valid_fills,
        "fill_rate": Decimal(valid_fills) / Decimal(len(orders)) if orders else Decimal("0"),
        "rejection_rate": Decimal(rejected_count) / Decimal(len(orders) + rejected_count) if orders or rejected_count else Decimal("0"),
        "trades": len(completed),
        "win_rate": Decimal(len(wins)) / Decimal(len(completed)) if completed else Decimal("0"),
        "average_win": gross_profit / Decimal(len(wins)) if wins else Decimal("0"),
        "average_loss": gross_loss / Decimal(len(losses)) if losses else Decimal("0"),
        "payoff_ratio": (gross_profit / Decimal(len(wins))) / (gross_loss / Decimal(len(losses))) if wins and losses and gross_loss else None,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "expectancy": sum(completed, Decimal("0")) / Decimal(len(completed)) if completed else Decimal("0"),
        "commissions": commissions,
        "slippage": slippage,
        "max_risk_usage": max((item.max_theoretical_risk for item in portfolio_snapshots), default=Decimal("0")),
        "max_capital_usage": max((max(Decimal("0"), item.initial_cash - item.cash) for item in portfolio_snapshots), default=Decimal("0")),
        "best_day": max(daily.values(), default=Decimal("0")),
        "worst_day": min(daily.values(), default=Decimal("0")),
        "tail_loss_5pct": _tail_loss(list(daily.values())),
        "returns_by_year": grouped_year,
        "returns_by_month": grouped_month,
        "pnl_by_entry_dte": grouped_dte,
        "pnl_by_entry_hour": grouped_hour,
        "pnl_by_iv_regime": grouped_iv,
        "terminal_orders": len(terminal_orders),
        **max_greeks,
    }


def _group_returns(equity, key):
    first, last = {}, {}
    for point in equity:
        group = key(point)
        first.setdefault(group, point.equity_liquidation)
        last[group] = point.equity_liquidation
    return {group: last[group] / value - 1 if value else Decimal("0") for group, value in first.items()}


def _tail_loss(values):
    if not values:
        return Decimal("0")
    ordered = sorted(values)
    count = max(1, int(len(ordered) * 0.05))
    return sum(ordered[:count], Decimal("0")) / Decimal(count)


def _max_drawdown_duration(equity):
    peak_time = equity[0].timestamp
    longest = 0.0
    for point in equity:
        if point.drawdown == 0:
            peak_time = point.timestamp
        else:
            longest = max(longest, (point.timestamp - peak_time).total_seconds())
    return Decimal(str(longest))


def _trade_groups(fills, settlements, completed_ids, market_slices, catalog):
    pnl_by_structure = defaultdict(Decimal)
    opening = {}
    for fill in fills:
        multiplier = _multiplier(catalog, fill.legs[0].instrument_id, fill.timestamp)
        pnl_by_structure[fill.structure_id] += fill.net_price * fill.quantity * multiplier - fill.commission
        if not fill.is_closing:
            opening.setdefault(fill.structure_id, fill)
    for settlement in settlements:
        pnl_by_structure[settlement.structure_id] += settlement.cash_delta
    markets = {item.timestamp: item for item in market_slices}
    dte_groups, hour_groups, iv_groups = defaultdict(Decimal), defaultdict(Decimal), defaultdict(Decimal)
    for structure_id, pnl in pnl_by_structure.items():
        if str(structure_id) not in completed_ids:
            continue
        fill = opening.get(structure_id)
        if not fill:
            continue
        definition = definition_at(catalog, fill.legs[0].instrument_id, fill.timestamp) if catalog else None
        spec = contract_spec(definition) if definition else None
        expiry = spec.expiry.date() if isinstance(spec,(ListedOptionSpec,CryptoOptionSpec)) else None
        dte = str((expiry - fill.timestamp.date()).days) if expiry else "unknown"
        dte_groups[dte] += pnl
        hour_groups[fill.timestamp.strftime("%H:00")] += pnl
        market = markets.get(fill.timestamp)
        ivs = []
        if market:
            by_instrument = {item.instrument_id: item for item in market.instruments}
            ivs = [by_instrument[leg.instrument_id].greeks.implied_volatility for leg in fill.legs if leg.instrument_id in by_instrument and by_instrument[leg.instrument_id].greeks and by_instrument[leg.instrument_id].greeks.implied_volatility is not None]
        average_iv = sum(ivs, Decimal("0")) / Decimal(len(ivs)) if ivs else None
        regime = "unknown" if average_iv is None else "low" if average_iv < Decimal("0.15") else "high" if average_iv > Decimal("0.30") else "medium"
        iv_groups[regime] += pnl
    return dict(dte_groups), dict(hour_groups), dict(iv_groups)


def _multiplier(catalog, instrument_id, at):
    if catalog is None:
        return Decimal("1")
    definition = definition_at(catalog, instrument_id, at)
    spec = contract_spec(definition)
    return getattr(spec, "multiplier", getattr(spec, "contract_size", Decimal("1")))
