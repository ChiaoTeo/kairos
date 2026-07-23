from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path

from kairospy.infrastructure.storage.codec import to_primitive


@dataclass(frozen=True, slots=True)
class RunSubscriptionSet:
    run_id: str
    active_streams: tuple[str, ...] = ()
    active_spaces: tuple[str, ...] = ()
    updated_at: str | None = None

    @classmethod
    def from_payload(cls, payload: object, *, run_id: str) -> "RunSubscriptionSet":
        data = payload if isinstance(payload, dict) else {}
        return cls(
            run_id=str(data.get("run_id") or run_id),
            active_streams=_sorted_texts(data.get("active_streams") or ()),
            active_spaces=_sorted_texts(data.get("active_spaces") or ()),
            updated_at=str(data.get("updated_at")) if data.get("updated_at") else None,
        )

    def apply(self, *, scope: str, operation: str, values: tuple[str, ...], at: datetime) -> "RunSubscriptionSet":
        if scope not in {"streams", "spaces"}:
            raise ValueError("subscription scope must be streams or spaces")
        if operation not in {"add", "remove", "set"}:
            raise ValueError("subscription operation must be add, remove, or set")
        values = _sorted_texts(values)
        streams = set(self.active_streams)
        spaces = set(self.active_spaces)
        target = streams if scope == "streams" else spaces
        if operation == "set":
            target.clear()
        if operation in {"add", "set"}:
            target.update(values)
        if operation == "remove":
            target.difference_update(values)
        return RunSubscriptionSet(
            run_id=self.run_id,
            active_streams=tuple(sorted(streams)),
            active_spaces=tuple(sorted(spaces)),
            updated_at=at.isoformat(),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "active_streams": list(self.active_streams),
            "active_spaces": list(self.active_spaces),
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class MarketDataSubscriptionRequest:
    run_id: str
    scope: str
    operation: str
    values: tuple[str, ...]
    actor: str = "strategy"
    reason: str = "market data subscription request"
    request_id: str | None = None

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("subscription request run_id is required")
        if self.scope not in {"streams", "spaces"}:
            raise ValueError("subscription request scope must be streams or spaces")
        if self.operation not in {"add", "remove", "set"}:
            raise ValueError("subscription request operation must be add, remove, or set")
        if not self.values:
            raise ValueError("subscription request values are required")
        if not self.actor.strip():
            raise ValueError("subscription request actor is required")
        if not self.reason.strip():
            raise ValueError("subscription request reason is required")

    @classmethod
    def streams(
        cls,
        run_id: str,
        operation: str,
        values: tuple[str, ...],
        *,
        actor: str = "strategy",
        reason: str = "market data stream subscription request",
        request_id: str | None = None,
    ) -> "MarketDataSubscriptionRequest":
        return cls(run_id, "streams", operation, _sorted_texts(values), actor=actor, reason=reason, request_id=request_id)

    @classmethod
    def spaces(
        cls,
        run_id: str,
        operation: str,
        values: tuple[str, ...],
        *,
        actor: str = "strategy",
        reason: str = "market data space subscription request",
        request_id: str | None = None,
    ) -> "MarketDataSubscriptionRequest":
        return cls(run_id, "spaces", operation, _sorted_texts(values), actor=actor, reason=reason, request_id=request_id)

    def payload(self) -> dict[str, object]:
        return {
            "scope": self.scope,
            "operation": self.operation,
            "values": list(_sorted_texts(self.values)),
            "request_id": self.idempotency_key,
            "requested_by": self.actor,
        }

    @property
    def idempotency_key(self) -> str:
        if self.request_id is not None and self.request_id.strip():
            return self.request_id
        payload = {
            "run_id": self.run_id,
            "scope": self.scope,
            "operation": self.operation,
            "values": list(_sorted_texts(self.values)),
            "actor": self.actor,
            "reason": self.reason,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return "subscription-request:" + sha256(encoded.encode("utf-8")).hexdigest()[:24]

    def submit(self, store: object, *, at: datetime):
        from kairospy.runtime.control import OperatorCommandBus, OperatorCommandType

        return OperatorCommandBus(store).submit(
            run_id=self.run_id,
            command_type=OperatorCommandType.UPDATE_SUBSCRIPTIONS,
            payload=self.payload(),
            actor=self.actor,
            reason=self.reason,
            idempotency_key=self.idempotency_key,
            at=at,
        )


@dataclass(frozen=True, slots=True)
class SubscriptionChangedEvent:
    run_id: str
    previous: RunSubscriptionSet
    current: RunSubscriptionSet
    added_streams: tuple[str, ...] = ()
    removed_streams: tuple[str, ...] = ()
    added_spaces: tuple[str, ...] = ()
    removed_spaces: tuple[str, ...] = ()
    command_id: str | None = None
    actor: str | None = None
    reason: str | None = None
    changed_at: str | None = None

    @classmethod
    def from_update(
        cls,
        *,
        previous: RunSubscriptionSet,
        current: RunSubscriptionSet,
        previous_session: "RunWorkspaceSession",
        current_session: "RunWorkspaceSession",
        command: object | None = None,
        at: datetime,
    ) -> "SubscriptionChangedEvent":
        previous_streams = _feed_streams(previous_session)
        current_streams = _feed_streams(current_session)
        return cls(
            run_id=current.run_id,
            previous=previous,
            current=current,
            added_streams=_sorted_texts(set(current_streams) - set(previous_streams)),
            removed_streams=_sorted_texts(set(previous_streams) - set(current_streams)),
            added_spaces=_sorted_texts(set(current.active_spaces) - set(previous.active_spaces)),
            removed_spaces=_sorted_texts(set(previous.active_spaces) - set(current.active_spaces)),
            command_id=str(getattr(command, "command_id", "")) if command is not None else None,
            actor=str(getattr(command, "actor", "")) if command is not None else None,
            reason=str(getattr(command, "reason", "")) if command is not None else None,
            changed_at=at.isoformat(),
        )

    def to_payload(self) -> dict[str, object]:
        return to_primitive({
            "event_type": "subscription_changed",
            "run_id": self.run_id,
            "previous": self.previous.to_payload(),
            "current": self.current.to_payload(),
            "added_streams": list(self.added_streams),
            "removed_streams": list(self.removed_streams),
            "added_spaces": list(self.added_spaces),
            "removed_spaces": list(self.removed_spaces),
            "command_id": self.command_id,
            "actor": self.actor,
            "reason": self.reason,
            "changed_at": self.changed_at,
        })


@dataclass(frozen=True, slots=True)
class SubscriptionRemovalSafetyTransition:
    run_id: str
    removed_streams: tuple[str, ...]
    removed_spaces: tuple[str, ...]
    pause_new_orders: bool = True
    cancel_open_orders_policy: str = "requires_explicit_keep_open_or_bound_cancel_adapter"
    flatten_policy: str = "manual_or_strategy_policy_required"
    feed_stop_policy: str = "after_order_position_policy_completes"
    command_id: str | None = None
    actor: str | None = None
    reason: str | None = None
    transitioned_at: str | None = None

    @classmethod
    def from_changed_event(
        cls,
        event: SubscriptionChangedEvent,
        *,
        command: object | None = None,
        at: datetime,
    ) -> "SubscriptionRemovalSafetyTransition | None":
        if not event.removed_streams and not event.removed_spaces:
            return None
        return cls(
            run_id=event.run_id,
            removed_streams=event.removed_streams,
            removed_spaces=event.removed_spaces,
            command_id=str(getattr(command, "command_id", "")) if command is not None else event.command_id,
            actor=str(getattr(command, "actor", "")) if command is not None else event.actor,
            reason=str(getattr(command, "reason", "")) if command is not None else event.reason,
            transitioned_at=at.isoformat(),
        )

    def to_payload(self) -> dict[str, object]:
        return to_primitive({
            "event_type": "subscription_removal_safety_transition",
            "run_id": self.run_id,
            "removed_streams": list(self.removed_streams),
            "removed_spaces": list(self.removed_spaces),
            "pause_new_orders": self.pause_new_orders,
            "cancel_open_orders_policy": self.cancel_open_orders_policy,
            "flatten_policy": self.flatten_policy,
            "feed_stop_policy": self.feed_stop_policy,
            "command_id": self.command_id,
            "actor": self.actor,
            "reason": self.reason,
            "transitioned_at": self.transitioned_at,
        })


@dataclass(frozen=True, slots=True)
class RuntimeFeedReconciliation:
    run_id: str
    added_streams: tuple[str, ...]
    removed_streams: tuple[str, ...]
    targets: tuple[dict[str, object], ...]
    reconciled_at: str
    status: str = "updated"

    @classmethod
    def from_sessions(
        cls,
        *,
        previous: "RunWorkspaceSession",
        current: "RunWorkspaceSession",
        lake_root: str | Path | None,
        at: datetime,
    ) -> "RuntimeFeedReconciliation":
        previous_plans = _ready_stream_plans(previous)
        current_plans = _ready_stream_plans(current)
        previous_streams = set(previous_plans)
        current_streams = set(current_plans)
        added = _sorted_texts(current_streams - previous_streams)
        removed = _sorted_texts(previous_streams - current_streams)
        kept = _sorted_texts(current_streams & previous_streams)
        targets = [
            *_feed_targets("start", added, current_plans, lake_root),
            *_feed_targets("keep", kept, current_plans, lake_root),
            *_feed_targets("stop", removed, previous_plans, lake_root),
        ]
        return cls(
            run_id=current.run_id,
            added_streams=added,
            removed_streams=removed,
            targets=tuple(targets),
            reconciled_at=at.isoformat(),
        )

    def to_payload(self) -> dict[str, object]:
        return to_primitive({
            "event_type": "runtime_feed_reconciled",
            "run_id": self.run_id,
            "status": self.status,
            "added_streams": list(self.added_streams),
            "removed_streams": list(self.removed_streams),
            "targets": list(self.targets),
            "reconciled_at": self.reconciled_at,
            "service_policy": "feed connector start/stop is performed by the bound runtime feed manager",
        })


def _sorted_texts(values: object) -> tuple[str, ...]:
    if isinstance(values, str):
        items = values.split(",")
    else:
        try:
            items = list(values)  # type: ignore[arg-type]
        except TypeError:
            items = []
    return tuple(sorted({str(item).strip() for item in items if str(item).strip()}))


def _feed_streams(session: "RunWorkspaceSession") -> tuple[str, ...]:
    feed_plan = session.feed_plan if isinstance(session.feed_plan, dict) else {}
    raw = feed_plan.get("streams")
    if not isinstance(raw, list):
        return ()
    return _sorted_texts(
        item.get("stream")
        for item in raw
        if isinstance(item, dict) and item.get("status") == "ready" and item.get("stream")
    )


def _ready_stream_plans(session: "RunWorkspaceSession") -> dict[str, dict[str, object]]:
    feed_plan = session.feed_plan if isinstance(session.feed_plan, dict) else {}
    raw = feed_plan.get("streams")
    if not isinstance(raw, list):
        return {}
    plans: dict[str, dict[str, object]] = {}
    for item in raw:
        if not isinstance(item, dict) or item.get("status") != "ready" or not item.get("stream"):
            continue
        plans[str(item["stream"])] = dict(item)
    return plans


def _feed_targets(
    action: str,
    streams: tuple[str, ...],
    plans: dict[str, dict[str, object]],
    lake_root: str | Path | None,
) -> list[dict[str, object]]:
    targets: list[dict[str, object]] = []
    for stream in streams:
        plan = plans[stream]
        dataset = str(plan.get("dataset") or stream)
        live_root = _ensure_live_root(dataset, lake_root) if action in {"start", "keep"} else _live_root(dataset, lake_root)
        targets.append({
            "stream": stream,
            "dataset": dataset,
            "provider": str(plan.get("provider") or ""),
            "venue": str(plan.get("venue") or ""),
            "source_plan": plan.get("source_plan") if isinstance(plan.get("source_plan"), dict) else {},
            "action": action,
            "service_status": {
                "start": "pending_start",
                "keep": "active",
                "stop": "pending_stop",
            }[action],
            **({"live_root": str(live_root)} if live_root is not None else {}),
        })
    return targets


def _ensure_live_root(dataset: str, lake_root: str | Path | None) -> Path | None:
    path = _live_root(dataset, lake_root)
    if path is not None:
        path.mkdir(parents=True, exist_ok=True)
    return path


def _live_root(dataset: str, lake_root: str | Path | None) -> Path | None:
    if lake_root is None:
        return None
    from kairospy.data import DatasetStore

    return DatasetStore(lake_root).live_path(dataset)


@dataclass(frozen=True, slots=True)
class RunWorkspaceSession:
    run_id: str
    subscription_set: RunSubscriptionSet
    feed_plan: dict[str, object]
    workspace_snapshot: dict[str, object] | None = None
    updated_at: str | None = None

    @classmethod
    def from_payload(cls, payload: object, *, run_id: str) -> "RunWorkspaceSession":
        data = payload if isinstance(payload, dict) else {}
        subscriptions = RunSubscriptionSet.from_payload(data.get("subscription_set"), run_id=run_id)
        workspace_snapshot = data.get("workspace_snapshot") if isinstance(data.get("workspace_snapshot"), dict) else None
        feed_plan = (
            data.get("feed_plan")
            if isinstance(data.get("feed_plan"), dict)
            else _feed_plan(subscriptions, workspace_snapshot=workspace_snapshot)
        )
        return cls(
            run_id=str(data.get("run_id") or run_id),
            subscription_set=subscriptions,
            feed_plan=dict(feed_plan),
            workspace_snapshot=workspace_snapshot,
            updated_at=str(data.get("updated_at")) if data.get("updated_at") else subscriptions.updated_at,
        )

    @classmethod
    def from_subscription_set(
        cls,
        subscription_set: RunSubscriptionSet,
        *,
        workspace_snapshot: dict[str, object] | None = None,
    ) -> "RunWorkspaceSession":
        return cls(
            run_id=subscription_set.run_id,
            subscription_set=subscription_set,
            feed_plan=_feed_plan(subscription_set, workspace_snapshot=workspace_snapshot),
            workspace_snapshot=workspace_snapshot,
            updated_at=subscription_set.updated_at,
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "subscription_set": self.subscription_set.to_payload(),
            "active_streams": list(self.subscription_set.active_streams),
            "active_spaces": list(self.subscription_set.active_spaces),
            "feed_plan": self.feed_plan,
            **({"workspace_snapshot": self.workspace_snapshot} if self.workspace_snapshot is not None else {}),
            "updated_at": self.updated_at,
        }


def _feed_plan(
    subscription_set: RunSubscriptionSet,
    *,
    workspace_snapshot: dict[str, object] | None = None,
) -> dict[str, object]:
    templates = _workspace_stream_templates(workspace_snapshot)
    expanded_by_space = {
        space: tuple(template.replace("{space}", space) for template in templates)
        for space in subscription_set.active_spaces
    }
    expanded_streams = _sorted_texts(
        stream
        for streams in expanded_by_space.values()
        for stream in streams
    )
    requested_streams = _sorted_texts((*subscription_set.active_streams, *expanded_streams))
    streams = [_stream_plan(stream) for stream in requested_streams]
    spaces = [_space_plan(space, expanded_by_space.get(space, ()), templates) for space in subscription_set.active_spaces]
    issues = [
        {
            "stream": str(item.get("stream")),
            "status": str(item.get("status")),
            "reason": str(item.get("reason") or ""),
        }
        for item in streams
        if item.get("status") != "ready"
    ]
    issues.extend(
        {
            "space": str(item.get("space")),
            "status": str(item.get("status")),
            "reason": str(item.get("reason") or ""),
        }
        for item in spaces
        if item.get("status") != "expanded"
    )
    return {
        "status": "ready" if not issues else "partial",
        "streams": streams,
        "spaces": spaces,
        "expanded_streams": list(expanded_streams),
        "stream_templates": list(templates),
        "issues": issues,
    }


def _space_plan(space: str, expanded_streams: tuple[str, ...], templates: tuple[str, ...]) -> dict[str, object]:
    if expanded_streams:
        return {
            "space": space,
            "status": "expanded",
            "streams": list(expanded_streams),
            "templates": list(templates),
        }
    return {
        "space": space,
        "status": "missing_template",
        "reason": "space subscription needs a Workspace stream template such as {space}.orderbook",
    }


def _stream_plan(stream: str) -> dict[str, object]:
    try:
        from kairospy.integrations.data_products.resolver import DataProductResolver

        plan = DataProductResolver().resolve(stream)
    except Exception as error:
        return {
            "stream": stream,
            "status": "unresolved",
            "reason": f"{type(error).__name__}: {error}",
        }
    status = "ready" if plan.capability in {"live", "both"} else "not_live"
    return {
        "stream": str(plan.target_stream),
        "space": str(plan.target_stream.space),
        "dataset": str(plan.dataset_id),
        "provider": plan.provider,
        "venue": plan.venue,
        "capability": plan.capability,
        "product_key": plan.product_key,
        "source_plan": plan.source_plan or {},
        "status": status,
        **({"reason": f"stream capability {plan.capability!r} is not live-capable"} if status != "ready" else {}),
    }


def _workspace_stream_templates(workspace_snapshot: dict[str, object] | None) -> tuple[str, ...]:
    if not isinstance(workspace_snapshot, dict):
        return ()
    candidates: list[object] = []
    for key in ("bindings", "attachments"):
        raw = workspace_snapshot.get(key)
        if isinstance(raw, dict):
            candidates.extend(raw.values())
    for key in ("market", "features"):
        raw = workspace_snapshot.get(key)
        if isinstance(raw, list):
            candidates.extend(raw)
    templates: list[str] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        stream = str(item.get("stream") or "")
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        if "{space}" in stream or params.get("template") is True or metadata.get("template") is True:
            if "{space}" in stream:
                templates.append(stream)
    return _sorted_texts(templates)
