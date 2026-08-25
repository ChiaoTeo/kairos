from __future__ import annotations

from pathlib import Path
import tomllib

from kairospy.surface.workbench import KairosWorkbenchApp
from kairospy.surface.workbench.preferences import _set_workbench_theme
from kairospy.surface.workbench.state import load_workbench_state
from kairospy.system.apps.workspace.application import WorkspaceApplication


def test_selected_theme_is_saved_in_project_manifest_and_restored(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceApplication().init_project(tmp_path / "trader")
    manifest = workspace.paths.manifest
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + '\n[market]\ndefault_profile = "paper"\n',
        encoding="utf-8",
    )
    state = load_workbench_state(workspace.paths.project_root)
    app = KairosWorkbenchApp(state)

    assert app.theme == "kairos-nord"
    assert app.select_theme("dracula")

    values = tomllib.loads(manifest.read_text(encoding="utf-8"))
    assert values["workbench"] == {"theme": "dracula"}
    assert values["market"] == {"default_profile": "paper"}

    restored = KairosWorkbenchApp(load_workbench_state(workspace.paths.project_root))
    assert restored.theme == "kairos-dracula"


def test_changing_theme_updates_existing_workbench_table(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init_project(tmp_path / "trader")
    manifest = workspace.paths.manifest
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + '\n[workbench]\n# Keep this project preference local.\ntheme = "nord"\n',
        encoding="utf-8",
    )
    app = KairosWorkbenchApp(load_workbench_state(workspace.paths.project_root))

    assert app.select_theme("everforest")

    content = manifest.read_text(encoding="utf-8")
    assert content.count("[workbench]") == 1
    assert "# Keep this project preference local." in content
    assert 'theme = "everforest"' in content


def test_theme_writer_uses_toml_section_line_breaks() -> None:
    assert _set_workbench_theme("version = 1\n", "nord") == (
        'version = 1\n\n[workbench]\ntheme = "nord"\n'
    )
    assert _set_workbench_theme(
        'version = 1\n\n[workbench]theme = "tokyo-night"\n', "dracula"
    ) == 'version = 1\n\n[workbench]\ntheme = "dracula"\n'
