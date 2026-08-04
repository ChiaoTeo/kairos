"""Account login application API."""

from __future__ import annotations

from datetime import datetime, timezone

from kairospy.application.usecases.account.protocol import AccountLoginPort, AccountLoginRequest, AccountLoginResult, AccountSession
from kairospy.domain.account import AccountContext


class AccountLoginApplicationService:
    def __init__(self, port: AccountLoginPort) -> None:
        self._port = port

    def login(
        self,
        context: AccountContext,
        *,
        credential_ref: str | None = None,
        connection_ids: tuple[str, ...] = (),
        at: datetime | None = None,
    ) -> AccountLoginResult:
        return self._port.login(
            AccountLoginRequest(
                context=context,
                credential_ref=credential_ref,
                connection_ids=connection_ids,
                observed_at=at or datetime.now(timezone.utc),
            )
        )

    def logout(self, session: AccountSession) -> None:
        self._port.logout(session)


__all__ = ["AccountLoginApplicationService", "AccountLoginResult"]
