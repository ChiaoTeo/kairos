#!/usr/bin/env python3
"""Create the credential-free workspace used for Workbench UI development."""

from __future__ import annotations

import argparse
from pathlib import Path

from kairospy.system.apps.workspace.application import WorkspaceApplication


DEFAULT_ROOT = Path(".agent-work/textual-agent-workflow/fixture")
DEFAULT_WORKSPACE_ID = "agent-ui-fixture"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create or validate the safe Kairos Workbench UI fixture."
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--workspace-id", default=DEFAULT_WORKSPACE_ID)
    arguments = parser.parse_args()

    project_root = arguments.root.resolve()
    application = WorkspaceApplication()
    manifest = project_root / ".kairos" / "kairos.toml"
    if manifest.is_file():
        workspace = application.open(project_root)
        if workspace.workspace_id != arguments.workspace_id:
            raise SystemExit(
                "existing fixture has workspace_id "
                f"{workspace.workspace_id!r}, expected {arguments.workspace_id!r}"
            )
    else:
        workspace = application.init_project(
            project_root,
            workspace_id=arguments.workspace_id,
        )

    print(workspace.paths.root)


if __name__ == "__main__":
    main()
