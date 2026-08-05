from decimal import Decimal
from datetime import datetime, timezone

from kairospy.application.usecases.account.application.runtime import InitialAssetBalance, SimulatedAccount, SimulatedAccountService
from kairospy.application.usecases.execution.application.runtime import build_execution_coordinator
from kairospy.domain.account import AccountBalance, AccountModel, AccountSegment, AccountSnapshot, AccountSource, AccountRuntimeContext, AssetCode, CollateralBalance, Environment, ProductFamily, SettlementPolicy, FeePolicy, derive_account_state


def test_simulated_account_initializes_multiple_assets() -> None:
    account = SimulatedAccount(
        "multi-asset",
        initial_balances=(
            InitialAssetBalance("USDT", Decimal("10000")),
            InitialAssetBalance("USDC", Decimal("5000")),
            InitialAssetBalance("BTC", Decimal("0.25")),
        ),
        broker="paper",
    )
    coordinator = build_execution_coordinator()
    SimulatedAccountService(account, coordinator.ledger)

    assert coordinator.ledger.balances(account.context.segment) == {
        "USDT": Decimal("10000"),
        "USDC": Decimal("5000"),
        "BTC": Decimal("0.25"),
    }


def test_collateral_balance_models_multi_currency_margin() -> None:
    collateral = CollateralBalance(
        "USDC",
        wallet=Decimal("5000"),
        available=Decimal("4500"),
        valuation=Decimal("4995"),
        haircut=Decimal("0.99"),
        source=AccountSource.VENUE,
    )

    assert collateral.asset == "USDC"
    assert collateral.available == Decimal("4500")
    assert collateral.haircut == Decimal("0.99")
    assert isinstance(collateral.asset, AssetCode)
    assert isinstance(AccountBalance.from_free_locked("usdt", Decimal("1"), Decimal("0"), source=AccountSource.VENUE).currency, AssetCode)
    assert SettlementPolicy(("USDT", "USDC")).currencies == (AssetCode("USDT"), AssetCode("USDC"))
    assert isinstance(FeePolicy(payment_currency="USDC").payment_currency, AssetCode)


def test_account_state_preserves_snapshot_collaterals() -> None:
    context = AccountRuntimeContext(
        AccountSegment("binance", "main", AccountModel.CONTRACT, ProductFamily.USD_M_FUTURES),
        Environment.LIVE,
    )
    snapshot = AccountSnapshot(
        context,
        balances=(),
        collaterals=(CollateralBalance("USDT", Decimal("100"), Decimal("90"), source=AccountSource.VENUE),),
        observed_at=datetime.now(timezone.utc),
    )

    state = derive_account_state(context, venue=snapshot)

    assert state.collaterals == snapshot.collaterals
