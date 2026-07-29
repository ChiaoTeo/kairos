from __future__ import annotations

from contextlib import AbstractContextManager, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, TextIO


class RunOutputLog(AbstractContextManager["RunOutputLog"]):
    def __init__(
        self,
        run_directory: str | Path,
        *,
        filename: str = "run.log",
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        self.path = Path(run_directory) / filename
        self.stdout = stdout
        self.stderr = stderr
        self._file: TextIO | None = None
        self._stdout_redirect: redirect_stdout[TextIO] | None = None
        self._stderr_redirect: redirect_stderr[TextIO] | None = None

    def __enter__(self) -> "RunOutputLog":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a", encoding="utf-8")
        out = _TeeTextIO(self._file, self.stdout) if self.stdout is not None else self._file
        err = _TeeTextIO(self._file, self.stderr) if self.stderr is not None else self._file
        self._stdout_redirect = redirect_stdout(out)
        self._stderr_redirect = redirect_stderr(err)
        self._stdout_redirect.__enter__()
        self._stderr_redirect.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool | None:
        try:
            if self._stderr_redirect is not None:
                self._stderr_redirect.__exit__(exc_type, exc_value, traceback)
            if self._stdout_redirect is not None:
                self._stdout_redirect.__exit__(exc_type, exc_value, traceback)
        finally:
            if self._file is not None:
                self._file.close()
        return None


class _TeeTextIO:
    def __init__(self, file: TextIO, stream: TextIO) -> None:
        self.file = file
        self.stream = stream

    def write(self, text: str) -> int:
        self.file.write(text)
        self.stream.write(text)
        return len(text)

    def flush(self) -> None:
        self.file.flush()
        self.stream.flush()

    def isatty(self) -> bool:
        return self.stream.isatty()

    @property
    def encoding(self) -> str | None:
        return self.stream.encoding


def write_run_log_section(
    run_directory: str | Path,
    title: str,
    values: Mapping[str, object],
    *,
    filename: str = "run.log",
    stdout: TextIO | None = None,
) -> None:
    path = Path(run_directory) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "",
        f"[{datetime.now(timezone.utc).isoformat()}] {title}",
        *[f"  {key}: {_format_value(value)}" for key, value in values.items()],
    ]
    text = "\n".join(lines) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)
    if stdout is not None:
        stdout.write(text)
        stdout.flush()


def _format_value(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "value") and isinstance(getattr(value, "value"), (str, int, float, bool)):
        return str(getattr(value, "value"))
    return str(value)


__all__ = ["RunOutputLog", "write_run_log_section"]
