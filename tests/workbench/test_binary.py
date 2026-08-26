from __future__ import annotations

import asyncio
from pathlib import Path
import sys

from kairospy.system.apps.workspace.application import WorkspaceApplication
from tui_test import TuiTest


ROOT = Path(__file__).resolve().parents[2]


def test_real_workbench_binary_is_agent_drivable_and_responsive(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "visual-fixture",
        workspace_id="visual-fixture",
    )

    async def run() -> None:
        async with TuiTest(session="kairos-workbench-smoke") as terminal:
            await terminal.run(
                sys.executable,
                "-m",
                "kairospy",
                "interactive",
                "--workspace",
                str(workspace.paths.root),
                "--dry-run",
                "--no-exec",
                cols=80,
                rows=24,
                cwd=str(ROOT),
            )
            await terminal.wait_text("KAIROS", timeout=30_000)
            assert "KAIROS  ·  visual-fixture" in await terminal.text()
            assert "• 就绪" in await terminal.text()

            await terminal.type("2")
            await terminal.press("Enter")
            await asyncio.sleep(0.2)
            assert "visual-fixture / 市场标的" in await terminal.text()

            await terminal.type("/back")
            await terminal.press("Enter")
            await asyncio.sleep(0.2)
            assert "运行前检查" in await terminal.text()

            await terminal.type("/help")
            await terminal.press("Enter")
            await terminal.wait_idle(timeout=10_000)
            assert "/market [代码]" in await terminal.text()

            await terminal.type("/market")
            await terminal.press("Enter")
            await asyncio.sleep(0.2)
            assert "输入代码或名称" in await terminal.text()

            await terminal.press("Escape")
            await asyncio.sleep(0.2)
            assert "运行前检查" in await terminal.text()

            await terminal.resize(60, 20)
            await terminal.wait_idle(timeout=10_000)
            assert "输入编号或命令；Enter 提交" in await terminal.text()

            await terminal.press("Ctrl+Q")
            await terminal.wait_exit(timeout=10_000)

    asyncio.run(run())


def test_real_workbench_binary_preserves_input_across_terminal_resize(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "resize-fixture",
        workspace_id="resize-fixture",
    )

    async def run() -> None:
        async with TuiTest(session="kairos-workbench-resize") as terminal:
            await terminal.run(
                sys.executable,
                "-m",
                "kairospy",
                "interactive",
                "--workspace",
                str(workspace.paths.root),
                "--dry-run",
                "--no-exec",
                cols=100,
                rows=30,
                cwd=str(ROOT),
            )
            await terminal.wait_text("KAIROS", timeout=30_000)
            await terminal.type("/market AAPL")

            await terminal.resize(60, 20)
            await terminal.wait_idle(timeout=10_000)
            narrow = await terminal.text()
            assert "KAIROS  ·  resize-fixture" in narrow
            assert "/market AAPL" in narrow

            await terminal.resize(100, 30)
            await terminal.wait_text("/bottom", timeout=10_000)
            restored = await terminal.text()
            assert "• 就绪" in restored
            assert "/market AAPL" in restored

            await terminal.press("Ctrl+Q")
            await terminal.wait_exit(timeout=10_000)

    asyncio.run(run())
