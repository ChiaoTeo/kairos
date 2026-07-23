from __future__ import annotations

import json
import locale
import os
from typing import Iterable, Mapping, Sequence
from kairospy.surface.cli.rendering.run_output import (
    _format_target_legs,
    _human_health,
    _human_status,
    _incident_rows,
    _render_run_live_command,
    _render_run_live_status,
    _render_run_live_summary,
    _service_rows,
    render_run_config_result as _render_run_config_result,
    render_run_live_result as _render_run_live_result,
    render_run_start_result as _render_run_start_result,
)
from kairospy.surface.cli.rendering.text import (
    LABELS as _LABELS,
    SUPPORTED_LANGUAGES,
    TEXT as _TEXT,
    display as _display,
    display_status_cell as _display_status_cell,
    paragraph as _paragraph,
    passed as _passed,
    section as _section,
    table as _table,
)


def resolve_language(requested: str | None) -> str:
    if requested:
        return requested
    configured = os.environ.get("KAIROSPY_LANG")
    if configured in SUPPORTED_LANGUAGES:
        return configured
    raw = os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG")
    if not raw:
        raw = locale.getlocale()[0] or ""
    return "zh-CN" if raw.lower().startswith("zh") else "en-US"


def render_product_result(group: str, action: str, payload: Mapping[str, object], language: str) -> str:
    if (group, action) == ("run", "start"):
        return _render_run_start_result(payload, language)
    if group == "run" and str(payload.get("operation") or "") in {"config.validate", "config.explain"}:
        return _render_run_config_result(payload, language)
    if (group, action) == ("run", "live"):
        return _render_run_live_result(payload, language)

    messages, labels = _TEXT[language], _LABELS[language]
    passed = _passed(payload)
    status = f"✓ {messages['pass']}" if passed else f"✗ {messages['fail']}"
    title = messages.get(f"{group}.{action}", f"{group} {action}").format(status=status)
    lines = [title, ""]

    if group == "run":
        sections = (
            ("section.result", ("run_id", "mode", "status", "workspace", "passed", "decisions_count", "comparisons"), {}),
            ("section.data", ("dataset", "input_identity", "capture"), {}),
            ("section.strategy", ("entrypoint", "strategy_id"), {}),
            ("section.files", ("manifest", "artifact", "runtime_database", "run_workspace"), {}),
        )
    else:
        sections = (("section.result", ("status", "passed"), {}),)

    shown: set[str] = {"next"}
    for heading, keys, overrides in sections:
        rows = []
        for key in keys:
            if key in overrides or key in payload:
                rows.append((labels.get(key, key), _display(overrides.get(key, payload.get(key)), language)))
                shown.add(key)
        if rows:
            lines.extend(_section(messages[heading], rows))

    releases = payload.get("releases")
    if isinstance(releases, list) and releases:
        table_rows = [(str(item.get("strategy_id", "")), str(item.get("version", "")),
                       str(item.get("implementation", ""))) for item in releases if isinstance(item, dict)]
        headers = (("策略 ID", "版本", "实现") if language == "zh-CN" else
                   ("Strategy ID", "Version", "Implementation"))
        lines.extend(_table(messages["section.releases"], headers, table_rows))
        shown.add("releases")

    comparisons = payload.get("comparisons")
    if isinstance(comparisons, dict):
        headers = (("检查项", "结果") if language == "zh-CN" else ("Check", "Result"))
        table_rows = [(str(key), _display(value, language)) for key, value in comparisons.items()]
        lines.extend(_table(messages["section.validation"], headers, table_rows))
        shown.add("comparisons")

    if (group, action) == ("factor", "verify-sma"):
        bars, ready = int(payload.get("bars", 0)), int(payload.get("ready", 0))
        warmup = max(0, bars - ready)
        slow = warmup + 1 if warmup else 0
        explanation = messages["warming"].format(slow=slow, warmup=warmup)
        if passed:
            explanation += "\n" + messages["replay_meaning"]
        lines.extend(_paragraph(messages["section.explanation"], explanation))

    audit_keys = ("factor_hash", "decision_hash", "intent_hash", "audit_hash")
    audit_rows = [(labels.get(key, key), _display(payload[key], language)) for key in audit_keys
                  if key in payload and key not in shown]
    if audit_rows:
        lines.extend(_section(messages["section.audit"], audit_rows))
        shown.update(key for key in audit_keys if key in payload)

    remaining = [(labels.get(key, key), _display(value, language)) for key, value in payload.items()
                 if key not in shown and key not in {"next", "lesson", "hypothesis", "tutorial"}]
    if remaining and not any(key in {"releases", "strategy_spec", "execution_policy", "implementation",
                                     "factor_bindings", "conservative", "stress", "attribution", "factor", "decision", "intent"}
                             for key in payload):
        lines.extend(_section(messages["section.result"], remaining))

    if payload.get("lesson") and group != "tutorial":
        lines.extend(_paragraph(messages["section.explanation"], str(payload["lesson"])))
    if payload.get("next"):
        lines.extend(_paragraph(messages["section.next"], str(payload["next"])))
    return "\n".join(lines).rstrip()


def render_error(error: Exception, language: str, *, json_output: bool = False) -> str:
    code = _error_code(error)
    if json_output:
        return json.dumps({"error": {"code": code, "message": str(error)}}, ensure_ascii=False, indent=2)
    messages = _TEXT[language]
    return f"{messages['error.title']}\n\n  {error}\n\n{messages['error.help']}\n\nError code: {code}"


def render_status_table(
    title: str,
    rows: Sequence[Mapping[str, object]],
    *,
    columns: tuple[str, ...] = ("name", "status", "detail"),
) -> str:
    rendered = _render_rich_status_table(title, rows, columns)
    if rendered is not None:
        return rendered
    headers = tuple(column.replace("_", " ").title() for column in columns)
    table_rows = [tuple(_display_status_cell(row.get(column, "")) for column in columns) for row in rows]
    return "\n".join(_table(title, headers, table_rows)).rstrip()


def render_key_value_panel(title: str, rows: Sequence[tuple[str, object]]) -> str:
    rendered = _render_rich_key_value_panel(title, rows)
    if rendered is not None:
        return rendered
    return "\n".join(_section(title, [(label, _display_status_cell(value)) for label, value in rows])).rstrip()


def render_command_success(title: str, rows: Sequence[tuple[str, object]] = ()) -> str:
    if not rows:
        return title
    return render_key_value_panel(title, rows)


def render_next_steps(steps: Sequence[str]) -> str:
    if not steps:
        return ""
    rendered = _render_rich_next_steps(steps)
    if rendered is not None:
        return rendered
    return "\n".join(_paragraph("Next Steps", "\n".join(steps))).rstrip()


def render_data_catalog(products: Sequence[Mapping[str, object]]) -> str:
    rows: list[dict[str, object]] = []
    for product in products:
        releases = product.get("releases")
        release_count = len(releases) if isinstance(releases, list) else 0
        rows.append({
            "key": product.get("logical_key", ""),
            "layer": product.get("layer", ""),
            "releases": release_count,
            "primary_time": product.get("primary_time", ""),
            "title": product.get("title", ""),
        })
    return render_status_table(
        "Kairos Data Catalog", rows,
        columns=("key", "layer", "releases", "primary_time", "title"),
    )


def render_dataset_list(title: str, products: Sequence[Mapping[str, object]]) -> str:
    rows = []
    if not products:
        return render_status_table(title, rows, columns=("dataset", "status", "time", "ready_for", "blocked_for", "issues"))
    for item in products:
        if "status" in item or "ready_for" in item or "blocked_for" in item:
            ready_for = item.get("ready_for")
            blocked_for = item.get("blocked_for")
            issues = item.get("issues")
            rows.append({
                "dataset": item.get("dataset") or item.get("key") or "",
                "status": item.get("status", ""),
                "time": item.get("time", ""),
                "ready_for": ", ".join(str(value) for value in ready_for) if isinstance(ready_for, list) else ready_for or "",
                "blocked_for": ", ".join(str(value) for value in blocked_for) if isinstance(blocked_for, list) else blocked_for or "",
                "issues": ", ".join(str(value) for value in issues) if isinstance(issues, list) else issues or "",
            })
            continue
        selected = item.get("selected_release")
        rows.append({
            "key": item.get("logical_key") or item.get("key") or item.get("dataset") or "",
            "layer": item.get("layer", ""),
            "releases": item.get("release_count", ""),
            "selected": selected.get("version", "") if isinstance(selected, Mapping) else "",
            "primary_time": item.get("primary_time", ""),
            "title": item.get("title", ""),
        })
    if rows and ("status" in rows[0] or "ready_for" in rows[0]):
        return render_status_table(title, rows, columns=("dataset", "status", "time", "ready_for", "blocked_for", "issues"))
    return render_status_table(title, rows, columns=("key", "layer", "releases", "selected", "primary_time", "title"))


def render_builtin_data_products(title: str, products: Sequence[Mapping[str, object]]) -> str:
    lines = [title]
    for item in products:
        lines.extend([
            "",
            f"Key: {item.get('key') or ''}",
            f"Title: {item.get('title') or ''}",
            f"Capability: {item.get('capability') or ''}",
            f"Requires Account: {'yes' if item.get('requires_account') else 'no'}",
            f"Default Dataset: {item.get('default_dataset_name') or ''}",
        ])
        aliases = item.get("aliases")
        if isinstance(aliases, Sequence) and not isinstance(aliases, (str, bytes)) and aliases:
            lines.append("Aliases: " + ", ".join(str(alias) for alias in aliases))
    return "\n".join(lines).rstrip()


def render_dataset_releases(title: str, releases: Sequence[Mapping[str, object]]) -> str:
    rows = [{
        "key": item.get("logical_key", ""),
        "release_id": item.get("release_id", ""),
        "version": item.get("version", ""),
        "quality": item.get("quality_level", ""),
        "status": item.get("status", ""),
        "selected": "yes" if item.get("selected") else "",
    } for item in releases]
    return render_status_table(title, rows, columns=("key", "release_id", "version", "quality", "status", "selected"))


def render_dataset_detail(title: str, payload: Mapping[str, object]) -> str:
    rows = [(key.replace("_", " ").title(), value) for key, value in payload.items()
            if key not in {"sources", "releases", "dimensions"}]
    output = [render_key_value_panel(title, rows)]
    dimensions = payload.get("dimensions")
    if isinstance(dimensions, Mapping) and dimensions:
        output.append(render_key_value_panel("Dimensions", tuple((str(key), value) for key, value in dimensions.items())))
    releases = payload.get("releases")
    if isinstance(releases, Sequence) and not isinstance(releases, (str, bytes)) and releases:
        release_rows = [
            {
                "release_id": item.get("release_id", ""),
                "quality": item.get("quality_level", ""),
                "status": item.get("status", ""),
                "provider": item.get("provider", ""),
            }
            for item in releases if isinstance(item, Mapping)
        ]
        release_rows.extend(
            {"release_id": str(item), "quality": "", "status": "", "provider": ""}
            for item in releases if not isinstance(item, Mapping)
        )
        output.append(render_status_table("Releases", release_rows, columns=("release_id", "quality", "status", "provider")))
    return "\n\n".join(item for item in output if item)


def render_generic_payload(title: str, payload: Mapping[str, object]) -> str:
    rows = []
    for key, value in payload.items():
        if isinstance(value, (dict, list, tuple)):
            continue
        rows.append((key.replace("_", " ").title(), value))
    if rows:
        return render_key_value_panel(title, rows)
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _render_rich_status_table(
    title: str,
    rows: Sequence[Mapping[str, object]],
    columns: tuple[str, ...],
) -> str | None:
    try:
        from rich.console import Console
        from rich.table import Table
    except Exception:
        return None
    console = Console(force_terminal=False, color_system=None, width=120)
    table = Table(title=title, show_header=True, header_style="bold")
    for column in columns:
        table.add_column(column.replace("_", " ").title())
    for row in rows:
        table.add_row(*(_display_status_cell(row.get(column, "")) for column in columns))
    with console.capture() as capture:
        console.print(table)
    return capture.get().rstrip()


def _render_rich_key_value_panel(title: str, rows: Sequence[tuple[str, object]]) -> str | None:
    try:
        from rich.console import Console
        from rich.table import Table
    except Exception:
        return None
    console = Console(force_terminal=False, color_system=None, width=120)
    table = Table(title=title, show_header=False)
    table.add_column("Field")
    table.add_column("Value")
    for label, value in rows:
        table.add_row(label, _display_status_cell(value))
    with console.capture() as capture:
        console.print(table)
    return capture.get().rstrip()


def _render_rich_next_steps(steps: Sequence[str]) -> str | None:
    try:
        from rich.console import Console
        from rich.panel import Panel
    except Exception:
        return None
    console = Console(force_terminal=False, color_system=None, width=120)
    body = "\n".join(f"{index}. {step}" for index, step in enumerate(steps, start=1))
    with console.capture() as capture:
        console.print(Panel(body, title="Next Steps"))
    return capture.get().rstrip()


def _error_code(error: Exception) -> str:
    text = str(error)
    if "dataset" in text.lower() or "input metadata" in text.lower():
        return "INPUT_DATASET_REQUIRED"
    if isinstance(error, FileNotFoundError):
        return "ARTIFACT_NOT_FOUND"
    if isinstance(error, PermissionError):
        return "DATASET_NOT_APPROVED"
    if isinstance(error, LookupError):
        return "OBJECT_NOT_FOUND"
    return "PRODUCT_COMMAND_FAILED"
