"""Account v2 event contract."""

from .source import decode_account_event


def decode_event(payload: bytes):
    """Decode one Account v2 event into the Account transport record."""

    return decode_account_event(payload)


__all__ = ["decode_event"]
