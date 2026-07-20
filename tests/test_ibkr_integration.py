from __future__ import annotations

from kairos.domain.identity import InstitutionId

import os
import unittest

from kairos.ports import Environment
from kairos.connectors.ibkr.account_gateway import IbkrAccountGateway
from kairos.connectors.ibkr.session import IbkrSession
from kairos.connectors.ibkr.research import IbkrSpxwResearchProvider
from kairos.domain.identity import AccountKey, AccountType, VenueId
from kairos.research.spec import OptionChainCaptureSpec


@unittest.skipUnless(os.getenv("RUN_IBKR_INTEGRATION") == "1", "set RUN_IBKR_INTEGRATION=1 to connect to IBKR")
class IbkrIntegrationTests(unittest.TestCase):
    def test_readonly_underlying_snapshot(self) -> None:
        spec = OptionChainCaptureSpec()
        provider = IbkrSpxwResearchProvider(
            spec,
            host=os.getenv("IBKR_HOST", "127.0.0.1"),
            port=int(os.getenv("IBKR_PORT", "4001")),
            client_id=int(os.getenv("IBKR_CLIENT_ID", "91")),
            readonly=True,
        )
        try:
            provider.connect()
            underlying = provider.underlying(spec)
            self.assertTrue(provider.snapshot([underlying], __import__("uuid").uuid4()))
        finally:
            provider.disconnect()

    def test_paper_account_balances_positions_and_open_orders_are_queryable(self) -> None:
        session = IbkrSession(
            os.getenv("IBKR_HOST", "127.0.0.1"), int(os.getenv("IBKR_PORT", "4001")),
            int(os.getenv("IBKR_CLIENT_ID", "92")), True,
        )
        try:
            session.connect()
            account_id = os.getenv("IBKR_ACCOUNT") or session.ib.managedAccounts()[0]
            account = AccountKey(InstitutionId("ibkr"), account_id, AccountType.SECURITIES_MARGIN)
            state = IbkrAccountGateway(session, Environment.PAPER).account_state(account)
            self.assertEqual(state.account, account)
        finally:
            session.disconnect()
