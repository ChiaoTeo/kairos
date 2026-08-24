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
            await terminal.wait_text("Kairos Workbench", timeout=30_000)
            assert "输入 help 查看命令" in await terminal.text()

            await terminal.type("help")
            await terminal.press("Enter")
            await terminal.wait_idle(timeout=10_000)
            assert "market [代码]" in await terminal.text()

            await terminal.type("market")
            await terminal.press("Enter")
            await terminal.wait_idle(timeout=10_000)
            assert "market" in await terminal.text()

            await terminal.press("Escape")
            await terminal.wait_idle(timeout=10_000)
            assert "就绪" in await terminal.text()

            await terminal.resize(60, 20)
            await terminal.wait_idle(timeout=10_000)
            assert "Kairos Workbench" in await terminal.text()

            await terminal.press("Ctrl+Q")
            await terminal.wait_exit(timeout=10_000)

    asyncio.run(run())
