from decimal import Decimal
import pytest

from kairospy.domain.account import (
    ExternalAccount,
    ExternalAccountIdentity,
    AccountModel,
    AccountModelChangedEvent,
    AccountTransitionStatus,
    AccountSegment,
    AccountSource,
    MarginMode,
    PositionSnapshot,
    ProductFamily,
)
from kairospy.application.usecases.account.application.account_model import (
    AccountModelApplicationService,
    SwitchAccountModelRequest,
)
from kairospy.application.usecases.account.application.capabilities import UnavailableAccountTransferService
from kairospy.application.usecases.account.protocol import AccountTransferRequest


def test_account_segment_separates_account_model_from_product_family() -> None:
    identity = ExternalAccountIdentity("binance", "main")
    segment = AccountSegment(
        identity,
        "usd-m-futures",
        model=AccountModel.CONTRACT,
        product_family=ProductFamily.USD_M_FUTURES,
        qualifier="linear",
    )

    assert segment.identity == identity
    assert segment.segment_id == "usd-m-futures"
    assert segment.model is AccountModel.CONTRACT
    assert segment.product_family is ProductFamily.USD_M_FUTURES
    assert segment.key == "usd-m-futures:contract:usd_m_futures:linear"


def test_account_owns_multiple_scopes() -> None:
    identity = ExternalAccountIdentity("binance", "main")
    account = ExternalAccount(identity)
    account = account.with_segment(AccountSegment(identity, "spot", AccountModel.NO_MARGIN, ProductFamily.SPOT))
    account = account.with_segment(AccountSegment(identity, "usd-m", AccountModel.CONTRACT, ProductFamily.USD_M_FUTURES))

    assert account.segment("spot").product_family is ProductFamily.SPOT
    assert account.segment("usd-m").model is AccountModel.CONTRACT


def test_contract_position_carries_margin_mode_separately_from_account_model() -> None:
    position = PositionSnapshot(
        instrument_id="BTC-PERP",
        quantity=Decimal("1"),
        source=AccountSource.VENUE,
        margin_mode=MarginMode.ISOLATED,
    )

    assert position.margin_mode is MarginMode.ISOLATED


def test_account_rejects_unified_and_independent_scopes_together() -> None:
    identity = ExternalAccountIdentity("okx", "main")
    with pytest.raises(ValueError, match="unified"):
        ExternalAccount(
            identity,
            (
                AccountSegment(identity, "unified", AccountModel.UNIFIED),
                AccountSegment(identity, "spot", AccountModel.NO_MARGIN, ProductFamily.SPOT),
            ),
        )


def test_account_model_switch_enters_reconciling_and_publishes_after_confirmation() -> None:
    identity = ExternalAccountIdentity("binance", "main")
    account = ExternalAccount(identity, (AccountSegment(identity, "spot", AccountModel.NO_MARGIN, ProductFamily.SPOT),), observed_model=AccountModel.NO_MARGIN)
    published: list[AccountModelChangedEvent] = []

    class Port:
        def switch(self, request):
            return type("Result", (), {
                "account": ExternalAccount(request.account.identity, (AccountSegment(request.account.identity, "unified", AccountModel.UNIFIED),), configured_model=request.target),
                "transition": request.account.request_model_switch(request.target)[1].__class__(request.account.identity, AccountModel.NO_MARGIN, request.target, AccountTransitionStatus.COMPLETED),
            })()

    class Publisher:
        def publish(self, event):
            published.append(event)

    result = AccountModelApplicationService(Port(), Publisher()).request_switch(
        SwitchAccountModelRequest(account, AccountModel.UNIFIED, reason="venue migration")
    )
    assert result.account.observed_model is AccountModel.UNIFIED
    assert result.account.status.value == "ready"
    assert len(published) == 1
    assert published[0].occurred_at.tzinfo is not None


def test_unavailable_transfer_is_explicitly_rejected() -> None:
    segment = AccountSegment("binance", "main", AccountModel.NO_MARGIN, ProductFamily.SPOT)
    result = UnavailableAccountTransferService().transfer(AccountTransferRequest(segment, "USDT", Decimal("10")))
    assert not result.accepted
    assert "adapter" in result.reason
