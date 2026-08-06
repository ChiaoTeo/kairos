"""Read and print the live account view exposed through StrategyContext."""

from __future__ import annotations

from kairospy.application.usecases.strategy.protocol import StrategyBase


class AccountContextLiveStrategy(StrategyBase):
    strategy_id = "example-account-context-live"

    def __init__(self, *, account: str, segment: str) -> None:
        self.account_key = account
        self.segment_key = segment

    def on_start(self, context) -> None:
        try:
            account = (
                context.accounts
                .account(self.account_key)
                .segment(self.segment_key)
                .view()
            )
        except (KeyError, RuntimeError, ValueError) as error:
            raise RuntimeError(
                "No live account view is available. Configure "
                "[accounts.main].ref and [strategy.params].segment with a "
                "connected read-only account and try again."
            ) from error

        print(
            f"account={account.identity} "
            f"stale={account.stale} "
            f"balances={len(account.balances)} "
            f"positions={len(account.positions)}",
            flush=True,
        )
        for balance in account.balances:
            print(
                f"balance currency={balance.currency} "
                f"total={balance.total} free={balance.free} locked={balance.locked}",
                flush=True,
            )
        for position in account.positions:
            print(
                f"position instrument={position.instrument_id} "
                f"quantity={position.quantity} "
                f"average_price={position.average_price}",
                flush=True,
            )


def strategy(account: str = "binance_zhaoqian888666", segment: str = "spot") -> AccountContextLiveStrategy:
    return AccountContextLiveStrategy(account=account, segment=segment)


__all__ = ["AccountContextLiveStrategy", "strategy"]
