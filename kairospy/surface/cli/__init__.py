from __future__ import annotations

import sys
from typing import Any, Sequence, TextIO


def execute_argv(argv: Sequence[str], stdout: TextIO) -> int:
    if argv and argv[0] == "data":
        from .commands.data import execute_data_argv

        return execute_data_argv(argv[1:], stdout)
    if argv and argv[0] == "research":
        from .commands.research import execute_research_argv

        return execute_research_argv(argv[1:], stdout)
    from .app import execute_argv as implementation

    return implementation(argv, stdout)


def main(argv: Sequence[str] | None = None) -> int:
    return execute_argv(sys.argv[1:] if argv is None else argv, sys.stdout)


def __getattr__(name: str) -> Any:
    if name == "app":
        from .app import app

        return app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["execute_argv", "main"]
