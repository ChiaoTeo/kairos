"""ExternalAccount directory capability used by account/system composition."""

from __future__ import annotations

from dataclasses import dataclass

from kairospy.domain.account import ExternalAccount, ExternalAccountIdentity, AccountSegment, AccountRuntimeContext, Environment
from kairospy.domain.reference import AccountSegmentId


@dataclass(frozen=True, slots=True)
class AccountBinding:
    alias: str
    index: int
    segments: tuple[AccountRuntimeContext, ...]
    ref: str = ""
    trade: bool = True

    def __post_init__(self) -> None:
        if not self.alias.strip():
            raise ValueError("account alias is required")
        if self.index < 0:
            raise ValueError("account index cannot be negative")
        if not self.segments:
            raise ValueError("account must expose at least one segment")
        identities = {(str(segment.identity.broker), str(segment.identity.account_id)) for segment in self.segments}
        if len(identities) != 1:
            raise ValueError("account segments must belong to one account identity")

    @property
    def account_key(self) -> str:
        first = self.segments[0]
        return ".".join(_key_part(part) for part in (first.identity.broker, first.identity.account_id) if part)

    def require_segment(self, segment: AccountSegmentId | str | None = None, *, environment: Environment | None = None) -> AccountRuntimeContext:
        candidates = self.segments if environment is None else tuple(item for item in self.segments if item.environment is environment)
        if segment is None:
            if len(candidates) == 1:
                return candidates[0]
            raise ValueError(f"multiple segments are available for account: {self.alias}; pass a segment key")
        key = str(segment)
        matches = tuple(context for context in candidates if key in _scope_match_keys(context.segment))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(f"multiple segments match account {self.alias!r} and segment {key!r}")
        raise KeyError(f"unknown account segment: {self.alias}.{key}")

    def default_segment(self, preferred: AccountSegment | None = None, *, environment: Environment | None = None) -> AccountRuntimeContext:
        if preferred is not None:
            for context in self.segments:
                if context.segment == preferred and (environment is None or context.environment is environment):
                    return context
        return self.require_segment(None, environment=environment)


@dataclass(frozen=True, slots=True)
class AccountDirectory:
    bindings: tuple[AccountBinding, ...] = ()

    def __post_init__(self) -> None:
        aliases = [binding.alias for binding in self.bindings]
        indexes = [binding.index for binding in self.bindings]
        if len(aliases) != len(set(aliases)):
            raise ValueError("account aliases must be unique")
        if len(indexes) != len(set(indexes)):
            raise ValueError("account indexes must be unique")

    @classmethod
    def from_contexts(cls, contexts: tuple[AccountRuntimeContext, ...]) -> "AccountDirectory":
        groups: dict[tuple[str, str], list[AccountRuntimeContext]] = {}
        for context in contexts:
            groups.setdefault((str(context.identity.broker), str(context.identity.account_id)), []).append(context)
        bindings: list[AccountBinding] = []
        for index, (_key, segments) in enumerate(sorted(groups.items())):
            alias = ".".join(_key_part(part) for part in (segments[0].identity.broker, segments[0].identity.account_id) if part)
            bindings.append(AccountBinding(alias, index, tuple(segments)))
        return cls(tuple(bindings))

    def require(self, key: str | int) -> AccountBinding:
        if isinstance(key, bool):
            raise ValueError("account key must be an alias or integer index")
        if isinstance(key, int):
            for binding in self.bindings:
                if binding.index == key:
                    return binding
            raise KeyError(f"unknown account index: {key}")
        text = key.strip()
        if not text:
            raise ValueError("account key cannot be empty")
        exact = tuple(binding for binding in self.bindings if text in {binding.alias, binding.account_key, binding.ref})
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise ValueError(f"multiple accounts match: {text}")
        matches = tuple(binding for binding in self.bindings if text in _identity_match_keys(binding))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(f"multiple accounts match identity: {text}")
        raise KeyError(f"unknown account: {text}")

    def account(self, key: str | int) -> ExternalAccount:
        binding = self.require(key)
        identity = binding.segments[0].identity
        segments = tuple({context.segment: context.segment for context in binding.segments}.values())
        return ExternalAccount(identity, segments)

    def contexts(self) -> tuple[AccountRuntimeContext, ...]:
        return tuple(context for binding in self.bindings for context in binding.segments)

    def resolve_context(self, *, account_id: str | None = None, account_index: int | None = None, segment: AccountSegmentId | str | None = None, environment: Environment | None = None, default: AccountRuntimeContext | None = None) -> AccountRuntimeContext:
        if account_index is not None:
            binding = self.require(account_index)
        elif account_id is not None:
            binding = self.require(str(account_id))
        elif default is not None:
            matches = tuple(binding for binding in self.bindings if default in binding.segments)
            if matches:
                binding = matches[0]
            elif len(self.bindings) == 1:
                binding = self.bindings[0]
            else:
                raise ValueError("multiple accounts are available; pass an account key")
        elif len(self.bindings) == 1:
            binding = self.bindings[0]
        else:
            raise ValueError("multiple accounts are available; pass an account key")
        selected_environment = environment if environment is not None else (None if default is None else default.environment)
        return binding.require_segment(segment, environment=selected_environment) if segment is not None else binding.default_segment(environment=selected_environment)


def _scope_match_keys(segment: AccountSegment) -> set[str]:
    return {segment.segment_id, segment.qualifier, ".".join(_key_part(part) for part in segment.key.split(":")), ".".join(_key_part(part) for part in (segment.broker, segment.account_id, segment.segment_id, segment.qualifier) if part)}


def _identity_match_keys(binding: AccountBinding) -> set[str]:
    return {key for context in binding.segments for key in (str(context.identity.account_id), str(context.identity.value))}


def _key_part(value: object) -> str:
    text = str(value).strip().lower()
    return "_".join(part for part in "".join(character if character.isalnum() else "_" for character in text).split("_") if part)


__all__ = ["AccountBinding", "AccountDirectory"]
