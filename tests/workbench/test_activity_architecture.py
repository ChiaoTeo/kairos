from __future__ import annotations

from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]
_WORKBENCH = _ROOT / "kairospy" / "surface" / "workbench"


def _source(relative: str) -> str:
    return (_WORKBENCH / relative).read_text(encoding="utf-8")


def test_activity_stream_has_no_transient_live_output_api() -> None:
    source = _source("widgets/workbench_log.py")

    for obsolete in (
        "begin_live_stream",
        "append_live_lines",
        "clear_live_stream",
        "end_live_stream",
    ):
        assert obsolete not in source


def test_model_conversation_does_not_recreate_chat_activities() -> None:
    source = _source("screens/flows/resources/configuration.py")

    assert "_chat_activity" not in source
    assert "ControlInteraction" in source
    assert "finish_model_chat" in source


def test_default_flow_results_do_not_pretty_print_raw_result_mappings() -> None:
    flow_root = _WORKBENCH / "screens" / "flows"
    occurrences: list[str] = []
    for path in flow_root.rglob("*.py"):
        if "Pretty(result" in path.read_text(encoding="utf-8"):
            occurrences.append(str(path.relative_to(flow_root)))

    assert occurrences == ["resources/views.py"]
    source = _source("screens/flows/resources/views.py")
    assert 'if action == "advanced":' in source


def test_runtime_status_views_do_not_nest_decorative_panels() -> None:
    reference = _source("screens/flows/reference/actions.py")
    launch = _source("screens/flows/launch/views.py")
    market = _source("screens/flows/market/workspace.py")

    assert "Panel(runtime" not in reference
    assert "Panel(summary" not in launch
    assert 'title="Market Runtime"' not in market
    assert 'title="Market Data Plane"' not in market
