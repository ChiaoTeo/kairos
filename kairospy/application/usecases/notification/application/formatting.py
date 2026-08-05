from __future__ import annotations


def notification_body(title: str, body: str) -> str:
    """Return the public, vendor-neutral text representation of a request."""

    return f"{title}\n{body}".strip()


__all__ = ["notification_body"]
