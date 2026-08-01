from __future__ import annotations

from dataclasses import dataclass

from kairospy.core.account import AccountBookRef, AccountContext


@dataclass(frozen=True, slots=True)
class LaunchAccountBinding:
    alias: str
    index: int
    books: tuple[AccountContext, ...]
    ref: str = ""
    trade: bool = True

    def __post_init__(self) -> None:
        if not self.alias.strip():
            raise ValueError("launch account alias is required")
        if self.index < 0:
            raise ValueError("launch account index cannot be negative")
        if not self.books:
            raise ValueError("launch account must expose at least one book")
        identities = {(str(book.identity.broker), str(book.identity.account_id), book.environment.value) for book in self.books}
        if len(identities) != 1:
            raise ValueError("launch account books must belong to one account identity and environment")

    @property
    def account_key(self) -> str:
        first = self.books[0]
        return ".".join(_key_part(part) for part in (first.identity.broker, first.identity.account_id) if part)

    def require_book(self, book: object | None = None) -> AccountContext:
        if book is None:
            if len(self.books) == 1:
                return self.books[0]
            raise ValueError(f"multiple books are available for account: {self.alias}; pass a book key")
        key = _book_key_text(book)
        matches = tuple(context for context in self.books if key in _book_match_keys(context.book))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(f"multiple books match account {self.alias!r} and book {key!r}")
        raise KeyError(f"unknown account book: {self.alias}.{key}")

    def default_book(self, preferred: AccountBookRef | None = None) -> AccountContext:
        if preferred is not None:
            for context in self.books:
                if context.book == preferred:
                    return context
        return self.require_book(None)


@dataclass(frozen=True, slots=True)
class LaunchAccountDirectory:
    bindings: tuple[LaunchAccountBinding, ...] = ()

    def __post_init__(self) -> None:
        aliases = [binding.alias for binding in self.bindings]
        indexes = [binding.index for binding in self.bindings]
        if len(aliases) != len(set(aliases)):
            raise ValueError("launch account aliases must be unique")
        if len(indexes) != len(set(indexes)):
            raise ValueError("launch account indexes must be unique")

    @classmethod
    def from_contexts(cls, contexts: tuple[AccountContext, ...]) -> "LaunchAccountDirectory":
        groups: dict[tuple[str, str, str], list[AccountContext]] = {}
        for context in contexts:
            groups.setdefault((context.environment.value, str(context.identity.broker), str(context.identity.account_id)), []).append(context)
        bindings = []
        for index, (_key, books) in enumerate(sorted(groups.items())):
            alias = ".".join(_key_part(part) for part in (books[0].identity.broker, books[0].identity.account_id) if part)
            bindings.append(LaunchAccountBinding(alias, index, tuple(books)))
        return cls(tuple(bindings))

    def require(self, key: str | int) -> LaunchAccountBinding:
        if isinstance(key, bool):
            raise ValueError("launch account key must be an alias or integer index")
        if isinstance(key, int):
            for binding in self.bindings:
                if binding.index == key:
                    return binding
            raise KeyError(f"unknown launch account index: {key}")
        text = key.strip()
        if not text:
            raise ValueError("launch account key cannot be empty")
        exact = tuple(
            binding
            for binding in self.bindings
            if text in {binding.alias, binding.account_key, binding.ref}
        )
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise ValueError(f"multiple launch accounts match: {text}")
        identity_matches = tuple(binding for binding in self.bindings if text in _identity_match_keys(binding))
        if len(identity_matches) == 1:
            return identity_matches[0]
        if len(identity_matches) > 1:
            raise ValueError(f"multiple launch accounts match account identity: {text}")
        raise KeyError(f"unknown launch account: {text}")

    def contexts(self) -> tuple[AccountContext, ...]:
        return tuple(context for binding in self.bindings for context in binding.books)

    def resolve_context(
        self,
        *,
        account_id: object | None = None,
        account_index: int | None = None,
        book: object | None = None,
        default: AccountContext | None = None,
    ) -> AccountContext:
        if account_index is not None:
            binding = self.require(account_index)
        elif account_id is not None:
            binding = self.require(str(account_id))
        elif default is not None:
            matches = tuple(binding for binding in self.bindings if default in binding.books)
            if matches:
                binding = matches[0]
            elif len(self.bindings) == 1:
                binding = self.bindings[0]
            else:
                raise ValueError("multiple launch accounts are available; pass an account key")
        elif len(self.bindings) == 1:
            binding = self.bindings[0]
        else:
            raise ValueError("multiple launch accounts are available; pass an account key")
        if book is not None:
            return binding.require_book(book)
        return binding.default_book(None if default is None else default.book)


def _book_match_keys(book: AccountBookRef) -> set[str]:
    return {
        str(book.book),
        book.qualifier,
        ".".join(_key_part(part) for part in book.book_key.split(":") if part),
        ".".join(_key_part(part) for part in (book.broker, book.account_id, book.book, book.qualifier) if part),
    }


def _book_key_text(value: object) -> str:
    return str(value)


def _identity_match_keys(binding: LaunchAccountBinding) -> set[str]:
    keys: set[str] = set()
    for context in binding.books:
        keys.add(str(context.identity.account_id))
        keys.add(str(context.identity.value))
    return keys


def _key_part(value: object) -> str:
    text = str(value).strip().lower()
    return "_".join(part for part in ("".join(character if character.isalnum() else "_" for character in text)).split("_") if part)


__all__ = ["LaunchAccountBinding", "LaunchAccountDirectory"]
