from __future__ import annotations

import unittest

from kairospy.identity import AccountRef, AccountType, InstitutionId


class AccountInstitutionIdentityTests(unittest.TestCase):
    def test_account_is_owned_by_institution(self) -> None:
        account = AccountRef(InstitutionId("IBKR"), "U123", AccountType.SECURITIES_MARGIN)
        self.assertEqual(account.institution_id, InstitutionId("ibkr"))
        self.assertEqual(account.value, "ibkr:securities_margin:U123")

    def test_non_institution_owner_is_rejected(self) -> None:
        with self.assertRaisesRegex(TypeError, "InstitutionId"):
            AccountRef("binance", "main", AccountType.DERIVATIVES)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
