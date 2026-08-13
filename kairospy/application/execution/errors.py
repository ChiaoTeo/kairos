from __future__ import annotations

from kairospy.domain_types import AccountId, IntentId, OrderId


class ExecutionLookupError(LookupError):
    """Base class for typed Strategy-facing Execution lookup failures."""


class ExecutionAccountNotEnabledError(ExecutionLookupError):
    def __init__(self, account_id: AccountId) -> None:
        self.account_id = account_id
        super().__init__(
            f"account '{account_id}' is not enabled for this Strategy launch"
        )


class IntentNotFoundError(ExecutionLookupError):
    def __init__(self, intent_id: IntentId) -> None:
        self.intent_id = intent_id
        super().__init__(f"Execution intent '{intent_id}' was not found")


class OrderNotFoundError(ExecutionLookupError):
    def __init__(self, order_id: OrderId) -> None:
        self.order_id = order_id
        super().__init__(f"Execution order '{order_id}' was not found")
