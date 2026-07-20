from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from kairospy.ports import AccountState, Environment, VenueBalance
from kairospy.connectors.ibkr.option_chain_provider import decimal_or_none
from kairospy.domain.identity import AssetId, InstitutionId, VenueId

from .session import IbkrSession


class IbkrAccountGateway:
    institution_id = InstitutionId("ibkr")
    venue_id = VenueId("ibkr")

    def __init__(self, session: IbkrSession, environment: Environment) -> None:
        self.session, self.environment = session, environment

    def account_state(self, account) -> AccountState:
        self.session.connect()
        summary = self.session.ib.accountSummary(account.account_id)
        balances_by_asset = {}
        for item in summary:
            if item.tag in {"CashBalance", "TotalCashValue"} and item.currency and item.currency != "BASE":
                value = decimal_or_none(item.value)
                if value is not None:
                    asset = AssetId(item.currency)
                    if item.tag == "TotalCashValue" or asset not in balances_by_asset:
                        balances_by_asset[asset] = value
        positions = []
        for position in self.session.ib.positions(account.account_id):
            matched = next((instrument_id for instrument_id, contract in self.session.contracts.items() if contract.conId == position.contract.conId), None)
            if matched:
                positions.append((matched, Decimal(str(position.position))))
        return AccountState(
            account,
            tuple(VenueBalance(asset, amount, amount) for asset, amount in balances_by_asset.items()),
            tuple(positions),
            tuple(str(item.order.orderId) for item in self.session.ib.openTrades()),
            datetime.now(timezone.utc),
        )
