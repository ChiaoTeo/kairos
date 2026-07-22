from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from kairospy.reference.contracts import ProductType


@dataclass(frozen=True, slots=True)
class TradingSession:
    trading_date: date
    opens_at: datetime
    closes_at: datetime


class TradingCalendar:
    """Minimal explicit US options calendar with injectable holiday dates."""

    def __init__(
        self,
        *,
        timezone_name: str = "America/New_York",
        opens_at: time = time(9, 30),
        closes_at: time = time(16, 0),
        holidays: frozenset[date] = frozenset(),
        early_closes: dict[date, time] | None = None,
    ) -> None:
        self.timezone = ZoneInfo(timezone_name)
        self.open_time = opens_at
        self.close_time = closes_at
        self.holidays = holidays
        self.early_closes = early_closes or {}

    def is_trading_day(self, value: date) -> bool:
        return value.weekday() < 5 and value not in self.holidays and value not in us_market_holidays(value.year)

    def session(self, value: date) -> TradingSession:
        if not self.is_trading_day(value):
            raise ValueError(f"not a trading day: {value}")
        close = self.early_closes.get(value, us_market_early_closes(value.year).get(value, self.close_time))
        return TradingSession(
            value,
            datetime.combine(value, self.open_time, self.timezone),
            datetime.combine(value, close, self.timezone),
        )

    def trading_days_between(self, start: date, end: date) -> tuple[date, ...]:
        days = []
        current = start
        while current <= end:
            if self.is_trading_day(current):
                days.append(current)
            current += timedelta(days=1)
        return tuple(days)

    def dte(self, current: date, expiry: date) -> int:
        if expiry < current:
            return -len(self.trading_days_between(expiry, current))
        return max(0, len(self.trading_days_between(current, expiry)) - (1 if self.is_trading_day(current) else 0))


class AlwaysOpenCalendar:
    timezone = ZoneInfo("UTC")

    @staticmethod
    def is_trading_day(value: date) -> bool:
        return True

    @staticmethod
    def dte(current: date, expiry: date) -> int:
        return (expiry - current).days

    @staticmethod
    def session(value: date) -> TradingSession:
        return TradingSession(
            value,
            datetime.combine(value, time(0), ZoneInfo("UTC")),
            datetime.combine(value, time(23, 59, 59), ZoneInfo("UTC")),
        )


class CalendarRegistry:
    def __init__(self, securities: TradingCalendar | None = None) -> None:
        self.securities = securities or TradingCalendar()
        self.always_open = AlwaysOpenCalendar()

    def for_product(self, product_type: ProductType):
        if product_type in {ProductType.CRYPTO_SPOT, ProductType.PERPETUAL, ProductType.CRYPTO_OPTION}:
            return self.always_open
        return self.securities


def us_market_holidays(year: int) -> frozenset[date]:
    holidays = {
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _easter_sunday(year) - timedelta(days=2),
        _last_weekday(year, 5, 0),
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _observed(date(year, 12, 25)),
    }
    if year >= 2022:
        holidays.add(_observed(date(year, 6, 19)))
    next_new_year = _observed(date(year + 1, 1, 1))
    if next_new_year.year == year:
        holidays.add(next_new_year)
    return frozenset(holidays)


def us_market_early_closes(year: int) -> dict[date, time]:
    candidates = {
        _nth_weekday(year, 11, 3, 4) + timedelta(days=1),
        date(year, 12, 24),
        date(year, 7, 3),
    }
    return {day: time(13) for day in candidates if day.weekday() < 5 and day not in us_market_holidays(year)}


def _observed(value: date) -> date:
    if value.weekday() == 5:
        return value - timedelta(days=1)
    if value.weekday() == 6:
        return value + timedelta(days=1)
    return value


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    value = date(year, month, 1)
    offset = (weekday - value.weekday()) % 7
    return value + timedelta(days=offset + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    value = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return value - timedelta(days=(value.weekday() - weekday) % 7)


def _easter_sunday(year: int) -> date:
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)
