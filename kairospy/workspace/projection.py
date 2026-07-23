from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class WorkspaceGraphNode:
    name: str
    kind: str
    source: str | None = None
    dataset: str | None = None
    stream: str | None = None
    view: str = "both"
    params: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("workspace graph node name is required")
        if not self.kind.strip():
            raise ValueError("workspace graph node kind is required")
        if self.view not in {"history", "live", "both"}:
            raise ValueError(f"unsupported workspace graph node view: {self.view!r}")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return {key: value for key, value in payload.items() if value not in (None, {}, ())}


@dataclass(frozen=True, slots=True)
class WorkspaceProjection:
    market: tuple[WorkspaceGraphNode, ...] = ()
    features: tuple[WorkspaceGraphNode, ...] = ()
    portfolio: tuple[str, ...] = ()
    treasury: tuple[str, ...] = ()
    attachments: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def nodes(self) -> tuple[WorkspaceGraphNode, ...]:
        return (*self.market, *self.features)

    def preflight(self, mode: str = "inspect") -> dict[str, Any]:
        issues: list[dict[str, Any]] = []
        nodes = self.nodes
        by_name = {node.name: node for node in nodes}
        attachment_names = set(self.attachments)
        for node in nodes:
            if str(node.source or "").startswith("missing_attachment:"):
                issues.append({
                    "severity": "warning" if node.params.get("required") is False else "error",
                    "code": "missing_attachment",
                    "node": node.name,
                    "source": node.source,
                    "message": f"workspace projection node {node.name!r} references an unconfigured attachment",
                })
            required_view = _required_runtime_view(mode)
            if required_view is not None and node.view not in {required_view, "both"}:
                issues.append({
                    "severity": "error",
                    "code": "view_not_available",
                    "node": node.name,
                    "view": node.view,
                    "required_view": required_view,
                    "message": f"workspace projection node {node.name!r} only allows {node.view!r}, but {mode!r} requires {required_view!r}",
                })
            if node.kind.startswith("feature:"):
                for source in _feature_sources(node):
                    if source and source not in by_name and source not in attachment_names:
                        issues.append({
                            "severity": "error",
                            "code": "missing_feature_source",
                            "node": node.name,
                            "source": source,
                            "message": f"feature node {node.name!r} references missing source node {source!r}",
                        })
        return {
            "mode": mode,
            "passed": not any(item["severity"] == "error" for item in issues),
            "issues": issues,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "attachments": self.attachments,
            "market": [item.to_dict() for item in self.market],
            "features": [item.to_dict() for item in self.features],
            "portfolio": list(self.portfolio),
            "treasury": list(self.treasury),
        }


class WorkspaceBuildContext:
    def __init__(self, *, project_root: str | Path, data_root: str | Path | None = None) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.data_root = Path(data_root).expanduser().resolve() if data_root is not None else self.project_root / ".kairos" / "data"
        self.attachments = WorkspaceAttachmentRegistry(self)
        self.features = WorkspaceFeatureBuilder(self)

    def attach(
        self,
        *,
        name: str,
        dataset: str | None = None,
        stream: str | None = None,
        view: str = "both",
        instruments: Iterable[str] = (),
        fields: Iterable[str] = (),
        freshness_seconds: float | None = None,
        **metadata: Any,
    ) -> WorkspaceGraphNode:
        if stream is None and dataset is None:
            raise ValueError("workspace attachment requires a Data Stream or Dataset")
        stream_id = str(stream or dataset)
        dataset_id = str(dataset or stream_id)
        template = "{space}" in stream_id
        node = WorkspaceGraphNode(
            name=name,
            kind="attachment",
            dataset=dataset_id,
            stream=stream_id,
            view=view,
            params={
                **({"template": True} if template else {}),
                **({"instruments": list(instruments)} if tuple(instruments) else {}),
                **({"fields": list(fields)} if tuple(fields) else {}),
                **({"freshness_seconds": freshness_seconds} if freshness_seconds is not None else {}),
                **metadata,
            },
        )
        self.attachments.add(node)
        return node

    def use(self, name: str) -> "WorkspaceAttachmentUse":
        return WorkspaceAttachmentUse(self, name)

    def resolve(self, *, name: str, kind: str, view: str = "both", **params: Any) -> WorkspaceGraphNode:
        return WorkspaceGraphNode(name=name, kind=kind, source="resolver", view=view, params=params)

    def project(
        self,
        *,
        market: Iterable[WorkspaceGraphNode] = (),
        features: Iterable[WorkspaceGraphNode] = (),
        portfolio: Iterable[str] = (),
        treasury: Iterable[str] = (),
    ) -> WorkspaceProjection:
        return WorkspaceProjection(
            market=tuple(market),
            features=tuple(features),
            portfolio=tuple(str(item) for item in portfolio),
            treasury=tuple(str(item) for item in treasury),
            attachments=self.attachments.to_dict(),
        )


class WorkspaceAttachmentRegistry:
    def __init__(self, context: WorkspaceBuildContext) -> None:
        self.context = context
        self._items: dict[str, WorkspaceGraphNode] = {}

    def add(self, node: WorkspaceGraphNode) -> None:
        existing = self._items.get(node.name)
        if existing is not None and existing.dataset != node.dataset:
            raise ValueError(f"workspace attachment {node.name!r} already points to {existing.dataset!r}")
        self._items[node.name] = node

    def use_profile(self, name: str) -> "WorkspaceAttachmentRegistry":
        from kairospy.workspace import WorkspaceRepository

        workspace = WorkspaceRepository.discover(self.context.project_root).open(name)
        for binding_name, binding in workspace.manifest.bindings.items():
            view = _binding_view(binding)
            self.add(WorkspaceGraphNode(
                name=binding_name,
                kind="attachment",
                dataset=binding.dataset,
                stream=getattr(binding, "stream", None) or binding.dataset,
                view=view,
                params=dict(binding.metadata),
            ))
        return self

    def as_ohlcv(self, attachment_name: str, *, name: str | None = None, view: str | None = None, **params: Any) -> WorkspaceGraphNode:
        return self.context.use(attachment_name).as_ohlcv(name=name or attachment_name, view=view, **params)

    def ohlcv(self, attachment_name: str, *, name: str | None = None, view: str | None = None, **params: Any) -> WorkspaceGraphNode:
        return self.as_ohlcv(attachment_name, name=name, view=view, **params)

    def as_orderbook(self, attachment_name: str, *, name: str | None = None, view: str | None = None, **params: Any) -> WorkspaceGraphNode:
        return self.context.use(attachment_name).as_orderbook(name=name or attachment_name, view=view, **params)

    def orderbook(self, attachment_name: str, *, name: str | None = None, view: str | None = None, **params: Any) -> WorkspaceGraphNode:
        return self.as_orderbook(attachment_name, name=name, view=view, **params)

    def as_mark_price(self, attachment_name: str, *, name: str | None = None, view: str | None = None, **params: Any) -> WorkspaceGraphNode:
        return self.context.use(attachment_name).as_mark_price(name=name or attachment_name, view=view, **params)

    def as_funding(self, attachment_name: str, *, name: str | None = None, view: str | None = None, **params: Any) -> WorkspaceGraphNode:
        return self.context.use(attachment_name).as_funding(name=name or attachment_name, view=view, **params)

    def get(self, name: str) -> WorkspaceGraphNode:
        try:
            return self._items[name]
        except KeyError as error:
            raise KeyError(f"workspace attachment is not configured: {name}") from error

    def to_dict(self) -> dict[str, dict[str, Any]]:
        return {
            name: node.to_dict()
            for name, node in sorted(self._items.items())
        }


class WorkspaceAttachmentUse:
    def __init__(self, context: WorkspaceBuildContext, attachment_name: str) -> None:
        self.context = context
        self.attachment_name = attachment_name

    def as_ohlcv(self, *, name: str, view: str | None = None, **params: Any) -> WorkspaceGraphNode:
        return self._node(name=name, kind="ohlcv", view=view, params=params)

    def ohlcv(self, *, name: str, view: str | None = None, **params: Any) -> WorkspaceGraphNode:
        return self.as_ohlcv(name=name, view=view, **params)

    def as_orderbook(self, *, name: str, view: str | None = None, **params: Any) -> WorkspaceGraphNode:
        return self._node(name=name, kind="orderbook", view=view, params=params)

    def orderbook(self, *, name: str, view: str | None = None, **params: Any) -> WorkspaceGraphNode:
        return self.as_orderbook(name=name, view=view, **params)

    def as_mark_price(self, *, name: str, view: str | None = None, **params: Any) -> WorkspaceGraphNode:
        return self._node(name=name, kind="mark_price", view=view, params=params)

    def as_funding(self, *, name: str, view: str | None = None, **params: Any) -> WorkspaceGraphNode:
        return self._node(name=name, kind="funding", view=view, params=params)

    def _node(self, *, name: str, kind: str, view: str | None, params: dict[str, Any]) -> WorkspaceGraphNode:
        try:
            attachment = self.context.attachments.get(self.attachment_name)
        except KeyError:
            if params.get("required") is False:
                return WorkspaceGraphNode(
                    name=name,
                    kind=kind,
                    source=f"missing_attachment:{self.attachment_name}",
                    view=view or "both",
                    params=params,
                )
            raise
        return WorkspaceGraphNode(
            name=name,
            kind=kind,
            source=f"attachment:{self.attachment_name}",
            dataset=attachment.dataset,
            stream=attachment.stream,
            view=view or attachment.view,
            params=params,
        )


class WorkspaceFeatureBuilder:
    def __init__(self, context: WorkspaceBuildContext) -> None:
        self.context = context

    def momentum(
        self,
        *,
        name: str,
        source: WorkspaceGraphNode,
        lookback: str | None = None,
        window: str | int | None = None,
        **params: Any,
    ) -> WorkspaceGraphNode:
        return self._feature(name=name, kind="momentum", source=source, lookback=str(lookback or window or ""), params=params)

    def realized_volatility(
        self,
        *,
        name: str,
        source: WorkspaceGraphNode,
        lookback: str | None = None,
        window: str | int | None = None,
        **params: Any,
    ) -> WorkspaceGraphNode:
        return self._feature(name=name, kind="realized_volatility", source=source, lookback=str(lookback or window or ""), params=params)

    def basis(self, *, name: str, short_leg: WorkspaceGraphNode, long_leg: WorkspaceGraphNode, **params: Any) -> WorkspaceGraphNode:
        return WorkspaceGraphNode(
            name=name,
            kind="feature:basis",
            source=f"{short_leg.name},{long_leg.name}",
            view=_combined_view(short_leg.view, long_leg.view),
            params=params,
        )

    def expected_funding_carry(self, *, name: str, funding: WorkspaceGraphNode, basis: WorkspaceGraphNode, **params: Any) -> WorkspaceGraphNode:
        return WorkspaceGraphNode(
            name=name,
            kind="feature:expected_funding_carry",
            source=f"{funding.name},{basis.name}",
            view=_combined_view(funding.view, basis.view),
            params=params,
        )

    def cross_venue_liquidity(self, *, name: str, short_book: WorkspaceGraphNode, long_book: WorkspaceGraphNode, **params: Any) -> WorkspaceGraphNode:
        return WorkspaceGraphNode(
            name=name,
            kind="feature:cross_venue_liquidity",
            source=f"{short_book.name},{long_book.name}",
            view=_combined_view(short_book.view, long_book.view),
            params=params,
        )

    def hedge_error(self, *, name: str, short_leg: WorkspaceGraphNode, long_leg: WorkspaceGraphNode, **params: Any) -> WorkspaceGraphNode:
        return WorkspaceGraphNode(
            name=name,
            kind="feature:hedge_error",
            source=f"{short_leg.name},{long_leg.name}",
            view=_combined_view(short_leg.view, long_leg.view),
            params=params,
        )

    def _feature(self, *, name: str, kind: str, source: WorkspaceGraphNode, lookback: str, params: dict[str, Any]) -> WorkspaceGraphNode:
        return WorkspaceGraphNode(
            name=name,
            kind=f"feature:{kind}",
            source=source.name,
            dataset=source.dataset,
            stream=source.stream,
            view=source.view,
            params={"lookback": lookback, **params},
        )


def _binding_view(binding: Any) -> str:
    raw = str(getattr(binding, "metadata", {}).get("view") or "").strip()
    if raw in {"history", "live", "both"}:
        return raw
    kind = getattr(binding, "kind", "")
    if kind == "dataset":
        return "history"
    if kind == "live_view":
        return "live"
    return "both"


def _combined_view(first: str, second: str) -> str:
    if first == second:
        return first
    if "live" in {first, second} and "history" in {first, second}:
        return "both"
    return "both"


def _required_runtime_view(mode: str) -> str | None:
    if mode in {"backtest", "historical-simulation"}:
        return "history"
    if mode in {"paper", "live"}:
        return "live"
    return None


def _feature_sources(node: WorkspaceGraphNode) -> tuple[str, ...]:
    source = str(node.source or "")
    if not source:
        return ()
    return tuple(item.strip() for item in source.split(",") if item.strip())
