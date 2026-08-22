from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Sequence, TextIO


def execute_argv(
    argv: Sequence[str], stdout: TextIO, *, prog_name: str = "kairospy"
) -> int:
    if argv and argv[0] == "data":
        from .commands.data import execute_data_argv

        return execute_data_argv(argv[1:], stdout)
    if argv and argv[0] == "research":
        from .commands.research import execute_research_argv

        return execute_research_argv(argv[1:], stdout)
    from .app import execute_argv as implementation

    return implementation(argv, stdout, prog_name=prog_name)


def main(argv: Sequence[str] | None = None) -> int:
    invoked_as = Path(sys.argv[0]).name
    prog_name = invoked_as if invoked_as in {"kairos", "kairospy"} else "kairos"
    return execute_argv(
        sys.argv[1:] if argv is None else argv,
        sys.stdout,
        prog_name=prog_name,
    )


def __getattr__(name: str) -> Any:
    if name == "app":
        from .app import app

        return app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["execute_argv", "main"]
