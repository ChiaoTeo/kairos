from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from kairospy.application.account import (
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
from kairospy.application.reference import InstrumentRef
from kairospy.application.account.mapping import map_accounts_snapshot
from kairospy.domain_types import AccountId, InstrumentId, SegmentKey


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
        broker="paper",
        environment="paper",
        account_model="no_margin",
        equity=Decimal("1000"),
        balances=(
            Balance(
                account_id,
                segment,
                "USDT",
                Decimal("100"),
                Decimal(available),
                Decimal("10"),
            ),
        ),
        positions=(
            Position(
                account_id,
                segment,
                instrument,
                Decimal("2"),
            ),
        ),
        freshness=DataFreshness.FRESH,
        generation=generation,
    )


class _Projection:
    def __init__(
        self, account_id: AccountId, generation: int, segments: tuple[SegmentKey, ...]
    ) -> None:
        self.account_id = account_id
        self.generation = generation
        self.segments = segments
        self.reads = 0

    def snapshot(self, account_id: AccountId) -> AccountSnapshot:
        assert account_id == self.account_id
        self.reads += 1
        return AccountSnapshot(
            account_id,
            tuple(
                _segment(
                    str(account_id),
                    segment,
                    generation=self.generation,
                    available="90" if segment == SPOT else "40",
                )
                for segment in self.segments
            ),
            self.generation,
        )


def test_accounts_chain_reads_each_account_mmap_once_and_preserves_order() -> None:
    main = _Projection(AccountId("main"), 7, (SPOT, CROSS_MARGIN))
    secondary = _Projection(AccountId("secondary"), 12, (SPOT,))
    application = AccountApplication(
        {
            AccountId("main"): main,
            AccountId("secondary"): secondary,
        }
    )

    accounts = application.accounts
    balance = accounts[0].segment(SPOT).require_balance("USDT")

    assert [str(value.account_id) for value in accounts] == ["main", "secondary"]
    assert balance.available == Decimal("90")
    assert main.reads == 1
    assert secondary.reads == 1
    assert accounts[0].generation == 7
    assert accounts[1].generation == 12


def test_required_segment_readiness_is_checked_from_account_current_view() -> None:
    account_id = AccountId("main")

    class Projection:
        lifecycle = SegmentSyncLifecycle.BOOTSTRAPPING

        def snapshot(self, requested: AccountId) -> AccountSnapshot:
            assert requested == account_id
            segment = replace(
                _segment("main", SPOT, generation=1, available="90"),
                sync_lifecycle=self.lifecycle,
            )
            return AccountSnapshot(account_id, (segment,), 1)

    projection = Projection()
    application = AccountApplication(
        {account_id: projection}, required_segments={account_id: ("spot",)}
    )

    with pytest.raises(RuntimeError, match="required segment spot is not ready"):
        application._check_event_source_ready()

    projection.lifecycle = SegmentSyncLifecycle.LIVE
    application._check_event_source_ready()


def test_account_reads_only_selected_account_and_segments_share_generation() -> None:
    main = _Projection(AccountId("main"), 7, (SPOT, CROSS_MARGIN))
    secondary = _Projection(AccountId("secondary"), 12, (SPOT,))
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
        1,
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
        1,
    )

    assert account.segment("provider_custom").segment_key == custom


def test_projection_mapper_groups_every_segment_without_cross_account_leakage() -> None:
    snapshot = map_accounts_snapshot(
        {
            "generation": 9,
            "accounts": [
                {
                    "account_id": "main",
                    "segment_key": "spot",
                    "broker": "binance",
                    "environment": "live",
                    "status": "ready",
                    "equity": "100",
                    "balances": [
                        {"asset_code": "USDT", "total": "100", "available": "90"}
                    ],
                    "positions": [],
                },
                {
                    "account_id": "main",
                    "segment_key": "usd_m_futures",
                    "broker": "binance",
                    "environment": "live",
                    "status": "ready",
                    "equity": "250",
                    "balances": [
                        {"asset_code": "USDT", "total": "250", "available": "220"}
                    ],
                    "positions": [
                        {
                            "instrument_id": "instrument:binance:BTCUSDT",
                            "position_side": "long",
                            "quantity": "2",
                        },
                        {
                            "instrument_id": "instrument:binance:BTCUSDT",
                            "position_side": "short",
                            "quantity": "-1",
                        },
                    ],
                },
                {
                    "account_id": "outside",
                    "segment_key": "spot",
                    "broker": "paper",
                    "environment": "paper",
                    "status": "ready",
                    "balances": [],
                    "positions": [],
                },
            ],
        },
        enabled_account_ids=(AccountId("main"),),
    )

    account = snapshot.account("main")
    assert [str(value.segment_key) for value in account.segments] == [
        "spot",
        "usd_m_futures",
    ]
    assert account.segment("spot").require_balance("USDT").total == Decimal("100")
    assert account.segment("usd_m_futures").require_balance("USDT").total == Decimal(
        "250"
    )
    futures_positions = account.segment("usd_m_futures").positions
    assert [value.position_side for value in futures_positions] == [
        PositionSide.LONG,
        PositionSide.SHORT,
    ]
    assert snapshot.find_account("outside") is None


def test_runtime_readiness_method_is_not_public_account_api() -> None:
    assert not hasattr(AccountApplication, "events")
    assert not hasattr(AccountApplication, "check_event_source_ready")
