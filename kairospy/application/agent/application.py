from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from threading import RLock

from .models import (
    AgentContextDocument,
    AgentContextReceipt,
    AgentContextSnapshot,
    AgentContextStatus,
    AgentMode,
    AgentModeReceipt,
    AgentModeStatus,
    DecisionAgentHealth,
)


class AgentApplication:
    """Small Strategy-facing control surface for context and runtime mode."""

    def __init__(
        self,
        *,
        enabled: bool,
        required: bool = False,
        initial_mode: AgentMode = AgentMode.SHADOW,
        selectable_modes: tuple[AgentMode, ...] = (),
    ) -> None:
        if not enabled and required:
            raise ValueError("A required Agent cannot be disabled")
        self._enabled = enabled
        self._required = required
        self._mode = initial_mode
        self._selectable_modes = frozenset(selectable_modes)
        self._mode_revision = 0
        self._context_watermark = 0
        self._documents: dict[str, tuple[int, AgentContextDocument]] = {}
        self._event_sequence: int | None = None
        self._event_time: datetime | None = None
        self._lock = RLock()
        self._health_provider = None
        self._runtime_metadata: dict[str, object] = {}

    @classmethod
    def disabled(cls) -> "AgentApplication":
        return cls(enabled=False)

    def _bind_event(self, sequence: int | None, occurred_at: datetime | None) -> None:
        with self._lock:
            self._event_sequence = sequence
            self._event_time = occurred_at

    def _bind_health_provider(self, provider) -> None:
        """Composition-only binding for worker diagnostics."""

        self._health_provider = provider

    def _bind_runtime_metadata(self, **values: object) -> None:
        """Composition-only non-secret runtime identity for diagnostics."""

        self._runtime_metadata = dict(values)

    def publish_context(self, document: AgentContextDocument) -> AgentContextReceipt:
        with self._lock:
            bound = self._bind_document_metadata(document)
            previous = self._documents.get(bound.key)
            if previous is None and len(self._documents) >= 128:
                return AgentContextReceipt(
                    bound.key,
                    self._context_watermark,
                    bound.content_hash,
                    AgentContextStatus.REJECTED,
                    "Agent context may contain at most 128 documents",
                )
            if previous is not None and previous[1].content_hash == bound.content_hash:
                return AgentContextReceipt(
                    bound.key,
                    previous[0],
                    bound.content_hash,
                    AgentContextStatus.DUPLICATE,
                )
            self._context_watermark += 1
            revision = self._context_watermark
            self._documents[bound.key] = (revision, bound)
            return AgentContextReceipt(
                bound.key,
                revision,
                bound.content_hash,
                AgentContextStatus.ACCEPTED,
            )

    def remove_context(self, key: str) -> AgentContextReceipt:
        normalized = key.strip()
        if not normalized:
            raise ValueError("Agent context key is required")
        with self._lock:
            previous = self._documents.pop(normalized, None)
            if previous is None:
                return AgentContextReceipt(
                    normalized,
                    self._context_watermark,
                    "",
                    AgentContextStatus.DUPLICATE,
                )
            self._context_watermark += 1
            return AgentContextReceipt(
                normalized,
                self._context_watermark,
                previous[1].content_hash,
                AgentContextStatus.REMOVED,
            )

    def set_mode(self, mode: AgentMode | str) -> AgentModeReceipt:
        requested = AgentMode(mode)
        with self._lock:
            if not self._enabled:
                return AgentModeReceipt(
                    requested,
                    self._mode,
                    self._mode_revision,
                    AgentModeStatus.REJECTED,
                    "Decision Agent is disabled by Launch",
                )
            if requested is self._mode:
                return AgentModeReceipt(
                    requested,
                    self._mode,
                    self._mode_revision,
                    AgentModeStatus.DUPLICATE,
                )
            if requested not in self._selectable_modes:
                return AgentModeReceipt(
                    requested,
                    self._mode,
                    self._mode_revision,
                    AgentModeStatus.REJECTED,
                    "Agent mode is not authorized by Launch",
                )
            self._mode = requested
            self._mode_revision += 1
            return AgentModeReceipt(
                requested,
                self._mode,
                self._mode_revision,
                AgentModeStatus.ACCEPTED,
            )

    def _snapshot(
        self,
        capability: str,
        *,
        now: datetime | None = None,
        required_contexts: tuple[str, ...] = (),
    ) -> AgentContextSnapshot:
        """Composition-only immutable snapshot pinned at candidate admission."""

        if not capability.strip():
            raise ValueError("Agent capability is required")
        current = now or self._event_time or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("Agent snapshot time must be timezone-aware")
        current = current.astimezone(timezone.utc)
        with self._lock:
            selected: list[tuple[str, int, AgentContextDocument]] = []
            for key, (revision, document) in self._documents.items():
                if capability not in document.scopes:
                    continue
                if document.expires_at is not None and document.expires_at <= current:
                    continue
                selected.append((key, revision, document))
            selected.sort(key=lambda item: item[0])
            present = {key for key, _, _ in selected}
            missing = tuple(key for key in required_contexts if key not in present)
            if missing:
                raise ValueError(
                    "Required Agent context is missing or expired: "
                    + ", ".join(missing)
                )
            fingerprint = [
                {"key": key, "revision": revision, "hash": document.content_hash}
                for key, revision, document in selected
            ]
            snapshot_hash = hashlib.sha256(
                json.dumps(fingerprint, sort_keys=True, separators=(",", ":")).encode(
                    "utf-8"
                )
            ).hexdigest()
            return AgentContextSnapshot(
                capability,
                self._mode,
                self._mode_revision,
                self._context_watermark,
                snapshot_hash,
                tuple(document for _, _, document in selected),
            )

    def _health(self) -> DecisionAgentHealth:
        """Composition/Launch diagnostic; Strategy protocol does not expose it."""

        worker = self._health_provider() if self._health_provider is not None else {}
        mcp_servers = self._runtime_metadata.get("mcp_servers", 0)
        return DecisionAgentHealth(
            enabled=self._enabled,
            required=self._required,
            state=str(
                worker.get("state", "disabled" if not self._enabled else "ready")
            ),
            mode=self._mode,
            mode_revision=self._mode_revision,
            context_watermark=self._context_watermark,
            context_documents=len(self._documents),
            queue_depth=int(worker.get("queue_depth", 0)),
            queue_capacity=int(worker.get("queue_capacity", 0)),
            in_flight=int(worker.get("in_flight", 0)),
            last_success_at=worker.get("last_success_at"),
            last_failure=worker.get("last_failure"),
            runtime=str(self._runtime_metadata.get("runtime"))
            if self._runtime_metadata.get("runtime") is not None
            else None,
            model=str(self._runtime_metadata.get("model"))
            if self._runtime_metadata.get("model") is not None
            else None,
            mcp_servers=(
                mcp_servers
                if isinstance(mcp_servers, int) and not isinstance(mcp_servers, bool)
                else 0
            ),
            store_ready=bool(self._runtime_metadata.get("store_ready", False)),
            rolling_error_rate=float(worker.get("rolling_error_rate", 0.0)),
            latency_p50_millis=worker.get("latency_p50_millis"),
            latency_p95_millis=worker.get("latency_p95_millis"),
            latency_p99_millis=worker.get("latency_p99_millis"),
        )

    def _bind_document_metadata(
        self, document: AgentContextDocument
    ) -> AgentContextDocument:
        if (
            document.observed_at is not None
            and document.source_event_sequence is not None
        ):
            return document
        return AgentContextDocument(
            key=document.key,
            scopes=document.scopes,
            values=document.values,
            observed_at=document.observed_at or self._event_time,
            expires_at=document.expires_at,
            source_event_sequence=(
                document.source_event_sequence
                if document.source_event_sequence is not None
                else self._event_sequence
            ),
        )


__all__ = ["AgentApplication"]
