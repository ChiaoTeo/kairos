from __future__ import annotations

from dataclasses import replace

import pytest

from kairospy.investment.apps.account.application import (
    CROSS_MARGIN,
    SPOT,
    AccountApplication,
    AccountSegmentNotFoundError,
    AccountSegmentSnapshot,
    AccountSnapshot,
    Balance,
    BalanceNotFoundError,
    DataFreshness,
    Position,
    PositionNotFoundError,
    PositionSide,
    SegmentSyncLifecycle,
)
from kairospy.investment.apps.reference.application import InstrumentRef
from kairospy.primitives.account import AccountId, BrokerId, SegmentKey
from kairospy.primitives.decimal import Money, Quantity, SignedQuantity
from kairospy.primitives.reference import AssetId, InstrumentId
from kairospy.primitives.time import Generation, Sequence


def _segment(
    account: str,
    segment: SegmentKey,
    *,
    generation: int,
    available: str,
) -> AccountSegmentSnapshot:
    account_id = AccountId(account)
    instrument = InstrumentRef(InstrumentId("instrument:test:BTCUSDT"), "BTCUSDT")
    return AccountSegmentSnapshot(
        account_id=account_id,
        segment_key=segment,
        broker=BrokerId("paper"),
        environment="paper",
        account_model="no_margin",
        equity=Money("1000"),
        balances=(
            Balance(
                account_id,
                segment,
                AssetId("USDT"),
                Quantity("100"),
                Quantity(available),
                Quantity("10"),
            ),
        ),
        positions=(
            Position(
                account_id,
                segment,
                instrument,
                SignedQuantity("2"),
            ),
        ),
        freshness=DataFreshness.FRESH,
        generation=Generation(generation),
    )


class _CurrentView:
    def __init__(
        self, account_id: AccountId, generation: int, segments: tuple[SegmentKey, ...]
    ) -> None:
        self.account_id = account_id
        self.generation = generation
        self.segments = segments
        self.reads = 0

    def snapshot(self) -> AccountSnapshot:
        account_id = self.account_id
        self.reads += 1
        segments = tuple(
            _segment(
                    str(account_id),
                    segment,
                    generation=self.generation,
                    available="90" if segment == SPOT else "40",
            )
            for segment in self.segments
        )
        return _contract_snapshot(account_id, segments, self.generation)


def _contract_snapshot(
    account_id: AccountId,
    segments: tuple[AccountSegmentSnapshot, ...],
    generation: int,
) -> AccountSnapshot:
    return AccountSnapshot(
        account_id=account_id,
        generation=Generation(generation),
        event_sequence=Sequence(generation),
        segments=segments,
    )


def test_accounts_chain_reads_each_account_current_view_once_and_preserves_order() -> None:
    main = _CurrentView(AccountId("main"), 7, (SPOT, CROSS_MARGIN))
    secondary = _CurrentView(AccountId("secondary"), 12, (SPOT,))
    application = AccountApplication(
        {
            AccountId("main"): main,
            AccountId("secondary"): secondary,
        }
    )

    accounts = application.accounts
    balance = accounts[0].segment(SPOT).require_balance("USDT")

    assert [str(value.account_id) for value in accounts] == ["main", "secondary"]
    assert balance.available == Quantity("90")
    assert main.reads == 1
    assert secondary.reads == 1
    assert accounts[0].generation == 7
    assert accounts[1].generation == 12


def test_required_segment_readiness_is_checked_from_account_current_view() -> None:
    account_id = AccountId("main")

    class CurrentView:
        lifecycle = SegmentSyncLifecycle.BOOTSTRAPPING

        def snapshot(self) -> AccountSnapshot:
            segment = replace(
                _segment("main", SPOT, generation=1, available="90"),
                sync_lifecycle=self.lifecycle,
            )
            return _contract_snapshot(account_id, (segment,), 1)

    current_view = CurrentView()
    application = AccountApplication(
        {account_id: current_view}, required_segments={account_id: ("spot",)}
    )

    with pytest.raises(RuntimeError, match="required segment spot is not ready"):
        application._check_event_source_ready()

    current_view.lifecycle = SegmentSyncLifecycle.LIVE
    application._check_event_source_ready()


def test_account_reads_only_selected_account_and_segments_share_generation() -> None:
    main = _CurrentView(AccountId("main"), 7, (SPOT, CROSS_MARGIN))
    secondary = _CurrentView(AccountId("secondary"), 12, (SPOT,))
    application = AccountApplication(
        {
            AccountId("main"): main,
            AccountId("secondary"): secondary,
        }
    )

    account = application.account("main")

    assert main.reads == 1
    assert secondary.reads == 0
    assert {value.generation for value in account.segments} == {7}
    assert account.segment(SPOT).balance("USDT") != account.segment(
        CROSS_MARGIN
    ).balance("USDT")


def test_segment_queries_have_optional_and_required_forms() -> None:
    account = AccountSnapshot(
        AccountId("main"),
        (_segment("main", SPOT, generation=1, available="90"),),
        Generation(1),
    )
    spot = account.segment("spot")

    assert spot.balance("UNKNOWN") is None
    assert spot.position(InstrumentId("instrument:test:UNKNOWN")) is None
    with pytest.raises(BalanceNotFoundError):
        spot.require_balance("UNKNOWN")
    with pytest.raises(PositionNotFoundError):
        spot.require_position(InstrumentId("instrument:test:UNKNOWN"))
    with pytest.raises(AccountSegmentNotFoundError):
        account.segment("options")


def test_custom_segment_keys_remain_open_ended() -> None:
    custom = SegmentKey("provider_custom")
    account = AccountSnapshot(
        AccountId("main"),
        (_segment("main", custom, generation=1, available="90"),),
        Generation(1),
    )

    assert account.segment("provider_custom").segment_key == custom


def test_runtime_readiness_method_is_not_public_account_api() -> None:
    assert not hasattr(AccountApplication, "events")
    assert not hasattr(AccountApplication, "check_event_source_ready")
