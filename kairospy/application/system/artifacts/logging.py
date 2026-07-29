from __future__ import annotations

from contextlib import AbstractContextManager, redirect_stderr, redirect_stdout
from pathlib import Path
import sys
from typing import TextIO


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
        self.stdout = stdout or sys.stdout
        self.stderr = stderr or sys.stderr
        self._file: TextIO | None = None
        self._stdout_redirect: redirect_stdout[TextIO] | None = None
        self._stderr_redirect: redirect_stderr[TextIO] | None = None

    def __enter__(self) -> "RunOutputLog":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a", encoding="utf-8")
        tee = _TeeTextIO(self._file, self.stdout)
        err_tee = _TeeTextIO(self._file, self.stderr)
        self._stdout_redirect = redirect_stdout(tee)
        self._stderr_redirect = redirect_stderr(err_tee)
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


__all__ = ["RunOutputLog"]
