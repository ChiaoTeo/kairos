from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py
from wheel.bdist_wheel import bdist_wheel as _bdist_wheel


class build_py(_build_py):
    """Compile and include the Rust process binaries in the wheel."""

    def run(self) -> None:
        super().run()
        output = Path(self.build_lib) / "kairospy" / "_bin"
        subprocess.run(
            [sys.executable, "scripts/build_rust_binaries.py", "--output", str(output)],
            check=True,
        )


class bdist_wheel(_bdist_wheel):
    """Mark wheels as platform-specific because they contain Rust binaries."""

    def finalize_options(self) -> None:
        super().finalize_options()
        self.root_is_pure = False


setup(cmdclass={"build_py": build_py, "bdist_wheel": bdist_wheel})
