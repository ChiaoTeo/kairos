from decimal import Decimal

from kairospy.application.usecases.portfolio.application import (
    PortfolioAvailability,
    PortfolioValuationApplication,
    PortfolioValuationRequest,
)
from kairospy.domain.account import AccountBalance, AccountRuntimeContext, ExternalAccountIdentity, AccountSegment, AccountState, AccountSource, Environment, ProductFamily, AccountModel, PositionSnapshot
from kairospy.domain.reference import ExternalAccountId, BrokerId


class Rates:
    def rate(self, currency: str, valuation_currency: str) -> Decimal | None:
        return {("BTC", "USD"): Decimal("60000")}.get((currency, valuation_currency))


def state(currency: str, amount: str, segment_id: str, *, positions: tuple[PositionSnapshot, ...] = ()) -> AccountState:
    segment = AccountSegment(
        ExternalAccountIdentity(BrokerId("demo"), ExternalAccountId("main")),
        segment_id,
        AccountModel.NO_MARGIN,
        ProductFamily.SPOT,
    )
    return AccountState(
        context=AccountRuntimeContext(segment, Environment.LIVE),
        balances=(AccountBalance(currency, Decimal(amount), Decimal(amount), Decimal("0"), AccountSource.VENUE),),
        margins=(), positions=positions, open_orders=(), observed_at=None, source=AccountSource.VENUE,
    )


def test_portfolio_converts_multiple_account_segments() -> None:
    result = PortfolioValuationApplication(Rates()).value(
        PortfolioValuationRequest((state("USD", "100", "spot-a"), state("BTC", "0.5", "spot-b")), "USD")
    )
    assert result.availability is PortfolioAvailability.READY
    assert result.equity == Decimal("30100.0")


def test_portfolio_is_unavailable_when_rate_is_missing() -> None:
    result = PortfolioValuationApplication(Rates()).value(
        PortfolioValuationRequest((state("EUR", "100", "spot-a"),), "USD")
    )
    assert result.availability is PortfolioAvailability.UNAVAILABLE
    assert result.equity is None
    assert result.unavailable_currencies == ("EUR",)


def test_portfolio_reports_exposure_and_unrealized_pnl() -> None:
    position = PositionSnapshot(
        "BTC-PERP",
        Decimal("0.5"),
        AccountSource.VENUE,
        mark_price=Decimal("60000"),
        unrealized_pnl=Decimal("100"),
        margin_currency="USD",
    )
    result = PortfolioValuationApplication(Rates()).value(
        PortfolioValuationRequest((state("USD", "100", "contract", positions=(position,)),), "USD")
    )
    assert result.availability is PortfolioAvailability.READY
    assert result.exposures[0].native_notional == Decimal("30000.0")
    assert result.unrealized_pnl == Decimal("100")
    assert result.equity == Decimal("200")
