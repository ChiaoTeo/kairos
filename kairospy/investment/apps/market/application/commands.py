"""Market application adapter over the Market JSON-RPC contract."""

from __future__ import annotations

from typing import Any, Mapping

from kairospy.infrastructure.transport.json_rpc import JsonRpcCaller
from kairospy.strategy import CommandHandle

from .requests import SubscriptionRequest


class MarketCommandClient:
    def __init__(
        self,
        client: JsonRpcCaller,
        *,
        launch_id: str | None = None,
        workspace_id: str = "workspace",
        market_runtime_id: str = "market",
    ) -> None:
        self.client = client
        self.launch_id = launch_id
        self.workspace_id = workspace_id
        self.market_runtime_id = market_runtime_id

    def _envelope(
        self,
        request_id: str,
        strategy_id: str,
        instance_id: str,
        *,
        operation: str,
        payload: Mapping[str, object],
    ) -> dict[str, object]:
        envelope: dict[str, object] = {
            "schema_version": 2,
            "command_id": request_id,
            "idempotency_key": request_id,
            "operation": operation,
            "strategy_id": strategy_id,
            "instance_id": instance_id,
            "payload": dict(payload),
        }
        if self.launch_id is not None:
            envelope["launch_id"] = self.launch_id
        return envelope

    def subscribe(
        self,
        request: SubscriptionRequest,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
    ) -> CommandHandle:
        body = self._envelope(
            request_id,
            strategy_id,
            instance_id,
            operation="subscribe",
            payload={
                "target": request.target.wire(),
                "observations": [
                    observation.wire() for observation in request.observations
                ],
                "provider_preference": request.provider_preference.wire(),
            },
        )
        return _handle(request_id, self.client.call("market_subscribe", [body]))

    def data_routes(self, query: Mapping[str, object] | None = None) -> dict[str, Any]:
        return dict(self.client.call("market_data_routes", [dict(query or {})]))

    def unsubscribe(
        self,
        subscription: object,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
    ) -> CommandHandle:
        if not instance_id.strip():
            return CommandHandle(
                request_id,
                "rejected",
                error="instance_id is required for market commands",
            )
        body = self._envelope(
            request_id,
            strategy_id,
            instance_id,
            operation="unsubscribe",
            payload={"subscription_id": str(subscription)},
        )
        return _handle(request_id, self.client.call("market_unsubscribe", [body]))

    def release_owner(
        self,
        *,
        strategy_id: str,
        instance_id: str,
        request_id: str,
    ) -> CommandHandle:
        body = self._envelope(
            request_id,
            strategy_id,
            instance_id,
            operation="release_owner",
            payload={},
        )
        return _handle(request_id, self.client.call("market_release_owner", [body]))


def _handle(request_id: str, value: Mapping[str, Any]) -> CommandHandle:
    status = str(value.get("status", "accepted"))
    result = value.get("result")
    return CommandHandle(
        request_id,
        status,
        result=(dict(result) if isinstance(result, Mapping) else dict(value)),
        error=None if value.get("error") is None else str(value["error"]),
    )


__all__ = ["MarketCommandClient"]
