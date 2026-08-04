from __future__ import annotations


def __getattr__(name: str) -> object:
    raise AttributeError(name)

__all__: list[str] = []
