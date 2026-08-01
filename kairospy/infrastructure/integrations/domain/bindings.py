from __future__ import annotations

from dataclasses import dataclass

from kairospy.infrastructure.integrations.domain.participants import ParticipantRole, integration_key


@dataclass(frozen=True, slots=True)
class ReferenceSourceRef:
    kind: ParticipantRole
    name: str
    market: str | None = None
    book: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", integration_key(self.name))
        if self.market is not None:
            object.__setattr__(self, "market", integration_key(self.market))
        if self.book is not None:
            object.__setattr__(self, "book", integration_key(self.book))


ParticipantKind = ParticipantRole


__all__ = ["ParticipantKind", "ReferenceSourceRef"]
