from __future__ import annotations

from kairospy.strategy import AccountEvent, Strategy, StrategyContext


class AccountSnapshotObserver(Strategy):
    """Read-only live strategy that reports the Account-owned current view."""

    strategy_id = "account-snapshot-observer"
    account_id = "manual-live-readonly"

    def on_start(self, ctx: StrategyContext) -> None:
        self._print_snapshot(ctx, reason="startup")

    def on_account(self, ctx: StrategyContext, event: AccountEvent) -> None:
        self._print_snapshot(ctx, reason=event.kind)

    def _print_snapshot(self, ctx: StrategyContext, *, reason: str) -> None:
        snapshot = ctx.account.account(self.account_id)
        print(
            "account snapshot "
            f"reason={reason} account={snapshot.account_id} "
            f"generation={snapshot.generation} "
            f"event_sequence={snapshot.event_sequence}",
            flush=True,
        )
        for segment in snapshot.segments:
            nonzero_balances = tuple(
                balance for balance in segment.balances if balance.total != 0
            )
            print(
                "account segment "
                f"segment={segment.segment_key} freshness={segment.freshness} "
                f"equity={segment.equity if segment.equity is not None else '-'} "
                f"balances={len(nonzero_balances)} positions={len(segment.positions)}",
                flush=True,
            )
            for balance in nonzero_balances:
                print(
                    "account balance "
                    f"segment={segment.segment_key} asset={balance.asset} "
                    f"total={balance.total} available={balance.available} "
                    f"reserved={balance.reserved}",
                    flush=True,
                )
            for position in segment.positions:
                print(
                    "account position "
                    f"segment={segment.segment_key} "
                    f"instrument={position.instrument.id} "
                    f"quantity={position.quantity} "
                    f"unrealized_pnl={position.unrealized_pnl}",
                    flush=True,
                )
