from __future__ import annotations

import argparse
import json

from kairospy.data.contracts import DatasetRelease, DatasetStatus
from kairospy.infrastructure.storage.codec import to_primitive

def emit_data_payload(args: argparse.Namespace, title: str, payload: object) -> None:
    from kairospy.surface.cli.output import (
        render_builtin_data_products, render_data_catalog, render_dataset_detail, render_dataset_list,
        render_dataset_releases, render_generic_payload, render_key_value_panel, render_status_table,
    )

    primitive = to_primitive(payload)
    if getattr(args, "action", None) != "audit":
        primitive = _hide_default_data_internals(primitive)
    if args.format == "json":
        print(json.dumps(primitive, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if not isinstance(primitive, dict):
        print(json.dumps(primitive, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if args.action == "catalog" and isinstance(primitive.get("products"), list):
        print(render_data_catalog(primitive["products"]))
        return
    if (
        args.action in {"product", "products"}
        and getattr(args, "product_action", None) == "list"
        and isinstance(primitive.get("products"), list)
    ):
        print(render_builtin_data_products("Kairos Built-In Data Products", primitive["products"]))
        return
    if args.action in {"product", "products"} and getattr(args, "product_action", None) == "doctor":
        print(_render_data_product_doctor_payload(title, primitive))
        return
    if args.action == "protocol":
        print(_render_data_protocol_payload(title, primitive))
        return
    if args.action == "use" and getattr(args, "list_products", False) and isinstance(primitive.get("products"), list):
        print(render_builtin_data_products("Kairos Built-In Data Products", primitive["products"]))
        return
    if args.action == "use" and primitive.get("operation") == "use":
        print(_render_data_use_payload(title, primitive))
        return
    if args.action == "list" and isinstance(primitive.get("datasets"), list):
        print(render_dataset_list(title, primitive["datasets"]))
        return
    if args.action == "releases" and isinstance(primitive.get("releases"), list):
        print(render_dataset_releases(title, primitive["releases"]))
        return
    if args.action == "search" and isinstance(primitive.get("datasets"), list):
        print(render_dataset_list(title, primitive["datasets"]))
        return
    if args.action == "acquire" and isinstance(primitive.get("products"), list):
        print(render_dataset_list(title, primitive["products"]))
        return
    if args.action in {"describe", "doctor"}:
        print(_render_data_doctor_payload(title, primitive))
        return
    if args.action == "diagnostics":
        print(render_status_table(title, _diagnostic_rows(primitive)))
        return
    if args.action == "acquire" and primitive.get("operation") == "acquire":
        print(_render_dataset_acquire_payload(title, primitive))
        return
    if args.action in {"plan", "acquire"}:
        print(_render_acquisition_plan_payload(title, primitive))
        return
    if args.action == "query":
        print(_render_query_payload(title, primitive))
        return
    if args.action == "replay":
        print(_render_replay_payload(title, primitive))
        return
    if args.action == "sample":
        print(_render_sample_payload(title, primitive))
        return
    print(render_generic_payload(title, primitive))

def hide_default_data_internals(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _hide_default_data_internals(item)
            for key, item in value.items()
            if key != "source_kind"
        }
    if isinstance(value, list):
        return [_hide_default_data_internals(item) for item in value]
    return value

def render_data_use_payload(title: str, payload: dict[str, object]) -> str:
    from kairospy.surface.cli.output import render_key_value_panel

    historical = payload.get("historical") if isinstance(payload.get("historical"), dict) else {}
    rows = [
        ("Product", payload.get("product", "")),
        ("Operation", payload.get("operation", "")),
        ("Dataset", payload.get("dataset", "")),
        ("Data Product", payload.get("data_product", "")),
        ("Default Dataset", payload.get("default_dataset", "")),
        ("Title", payload.get("title", "")),
        ("Capability", payload.get("capability", "")),
        ("Target Use", payload.get("target_use", "")),
        ("Status", historical.get("status", "")),
        ("Ready For", ", ".join(str(item) for item in historical.get("ready_for", ()))),
        ("Blocked For", ", ".join(str(item) for item in historical.get("blocked_for", ()))),
        ("Time", payload.get("time", "")),
        ("Requires Account", payload.get("requires_account", "")),
        ("Provider", payload.get("provider", "")),
        ("Venue", payload.get("venue", "")),
    ]
    return render_key_value_panel(title, [(label, value) for label, value in rows if value not in ("", None)])

def render_sample_payload(title: str, payload: dict[str, object]) -> str:
    from kairospy.surface.cli.output import render_key_value_panel

    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
    rows = [
        ("Product", payload.get("product", "")),
        ("Operation", payload.get("operation", "")),
        ("Source", payload.get("source", "")),
        ("Dataset", payload.get("dataset", "")),
        ("Provider", payload.get("provider", "")),
        ("Venue", payload.get("venue", "")),
        ("Market", runtime.get("market", "")),
        ("Symbol", runtime.get("symbol", "")),
        ("Channel", runtime.get("channel", "")),
        ("Levels", runtime.get("levels", "")),
        ("Interval", runtime.get("interval", "")),
        ("Stream", runtime.get("stream", "")),
        ("Limit", payload.get("limit", "")),
        ("Row Count", payload.get("row_count", "")),
    ]
    return render_key_value_panel(title, [(label, value) for label, value in rows if value not in ("", None)])

def diagnostic_rows(payload: dict[str, object]) -> list[dict[str, object]]:
    if isinstance(payload.get("checks"), list):
        return [
            {
                "name": item.get("name", item.get("check", "check")),
                "status": "ok" if item.get("passed", item.get("healthy", False)) else "warn",
                "detail": item.get("detail", item.get("message", "")),
            }
            for item in payload["checks"] if isinstance(item, dict)
        ]
    summary = payload.get("summary")
    if isinstance(summary, dict):
        return [{"name": key, "status": "ok" if not value else "warn", "detail": value} for key, value in summary.items()]
    healthy = payload.get("healthy", payload.get("passed", True))
    return [{"name": "data", "status": "ok" if healthy else "warn", "detail": "healthy" if healthy else "needs attention"}]

def render_data_doctor_payload(title: str, payload: dict[str, object]) -> str:
    from kairospy.surface.cli.output import render_key_value_panel

    def join_values(key: str) -> str:
        values = payload.get(key)
        if isinstance(values, list):
            return ", ".join(str(item) for item in values) if values else "-"
        return "-"

    rows = (
        ("Dataset", payload.get("dataset", "-")),
        ("Status", payload.get("status", "-")),
        ("Time", payload.get("time", "-")),
        ("Ready For", join_values("ready_for")),
        ("Blocked For", join_values("blocked_for")),
        ("Issues", join_values("issues")),
    )
    return render_key_value_panel(title, rows)

def dataset_acquire_payload(release: DatasetRelease) -> dict[str, object]:
    ready_for = _ready_for_dataset_status(release.status)
    all_uses = ("workspace", "backtest", "production")
    return {
        "product": "data",
        "operation": "acquire",
        "dataset": str(release.product_key),
        "status": _dataset_ready_status(release.status),
        "ready_for": ready_for,
        "blocked_for": [value for value in all_uses if value not in ready_for],
        "provider": release.provider,
        "venue": release.venue,
        "quality_level": release.quality_level.value,
        "format": release.format,
    }

def dataset_ready_status(status: DatasetStatus) -> str:
    if status is DatasetStatus.APPROVED_FOR_PRODUCTION:
        return "ready_for_production"
    if status is DatasetStatus.APPROVED_FOR_BACKTEST:
        return "ready_for_backtest"
    if status is DatasetStatus.APPROVED_FOR_WORKSPACE:
        return "ready_for_workspace"
    return status.value

def ready_for_dataset_status(status: DatasetStatus) -> list[str]:
    if status is DatasetStatus.APPROVED_FOR_PRODUCTION:
        return ["workspace", "backtest", "production"]
    if status is DatasetStatus.APPROVED_FOR_BACKTEST:
        return ["workspace", "backtest"]
    if status is DatasetStatus.APPROVED_FOR_WORKSPACE:
        return ["workspace"]
    return []

def render_dataset_acquire_payload(title: str, payload: dict[str, object]) -> str:
    from kairospy.surface.cli.output import render_key_value_panel

    def join_values(key: str) -> str:
        values = payload.get(key)
        if isinstance(values, list):
            return ", ".join(str(item) for item in values) if values else "-"
        return "-"

    rows = (
        ("Dataset", payload.get("dataset", "-")),
        ("Status", payload.get("status", "-")),
        ("Ready For", join_values("ready_for")),
        ("Blocked For", join_values("blocked_for")),
        ("Provider", payload.get("provider", "-")),
        ("Venue", payload.get("venue", "-")),
        ("Quality Level", payload.get("quality_level", "-")),
        ("Format", payload.get("format", "-")),
    )
    return render_key_value_panel(title, rows)

def render_data_product_doctor_payload(title: str, payload: dict[str, object]) -> str:
    from kairospy.surface.cli.output import render_key_value_panel, render_status_table

    aliases = payload.get("aliases")
    alias_text = ", ".join(str(alias) for alias in aliases) if isinstance(aliases, list) and aliases else "-"
    rows = (
        ("Data Product", payload.get("key") or payload.get("requested_key", "-")),
        ("Requested", payload.get("requested_key", "-")),
        ("Status", payload.get("status", "-")),
        ("Available", "yes" if payload.get("available") else "no"),
        ("Provider", payload.get("provider", "-")),
        ("Venue", payload.get("venue", "-")),
        ("Dataset", payload.get("dataset", "-")),
        ("Capability", payload.get("capability", "-")),
        ("Aliases", alias_text),
    )
    output = [render_key_value_panel(title, rows)]
    issues = payload.get("issues")
    if isinstance(issues, list) and issues:
        output.append(render_status_table(
            "Issues",
            [{"code": item.get("code", ""), "message": item.get("message", "")}
             for item in issues if isinstance(item, dict)],
            columns=("code", "message"),
        ))
    commands = payload.get("next_commands")
    if isinstance(commands, list) and commands:
        output.append(render_status_table(
            "Next Commands",
            [{"command": command} for command in commands],
            columns=("command",),
        ))
    return "\n\n".join(item for item in output if item)

def render_query_payload(title: str, payload: dict[str, object]) -> str:
    from kairospy.surface.cli.output import render_key_value_panel, render_status_table

    if payload.get("status") and "returned_rows" not in payload:
        issues = payload.get("issues")
        issue_codes = []
        if isinstance(issues, list):
            for issue in issues:
                if isinstance(issue, dict):
                    issue_codes.append(str(issue.get("code") or issue.get("message") or issue))
                else:
                    issue_codes.append(str(issue))
        return render_key_value_panel(title, (
            ("Dataset", payload.get("dataset", "-")),
            ("Status", payload.get("status", "-")),
            ("Issues", ", ".join(issue_codes) if issue_codes else "-"),
            ("Next Command", payload.get("next_command", "-")),
        ))

    output = [render_key_value_panel(title, (
        ("Dataset", payload.get("dataset", "-")),
        ("Returned Rows", payload.get("returned_rows", "-")),
        ("Total Rows", payload.get("total_rows", "-")),
    ))]
    rows = payload.get("rows")
    if isinstance(rows, list) and rows:
        fields = tuple(str(key) for key in rows[0].keys()) if isinstance(rows[0], dict) else ("row",)
        table_rows = []
        for row in rows:
            table_rows.append({field: row.get(field, "") for field in fields} if isinstance(row, dict) else {"row": row})
        output.append(render_status_table("Rows", table_rows, columns=fields))
    return "\n\n".join(output)

def render_replay_payload(title: str, payload: dict[str, object]) -> str:
    from kairospy.surface.cli.output import render_key_value_panel

    if payload.get("status") and "returned_rows" not in payload:
        issues = payload.get("issues")
        issue_codes = []
        if isinstance(issues, list):
            for issue in issues:
                if isinstance(issue, dict):
                    issue_codes.append(str(issue.get("code") or issue.get("message") or issue))
                else:
                    issue_codes.append(str(issue))
        return render_key_value_panel(title, (
            ("Dataset", payload.get("dataset", "-")),
            ("Status", payload.get("status", "-")),
            ("Issues", ", ".join(issue_codes) if issue_codes else "-"),
            ("Next Command", payload.get("next_command", "-")),
        ))

    output = [render_key_value_panel(title, (
        ("Dataset", payload.get("dataset", "-")),
        ("Returned Rows", payload.get("returned_rows", "-")),
        ("Total Rows", payload.get("total_rows", "-")),
    ))]
    rows = payload.get("rows")
    if isinstance(rows, list) and rows:
        output.append("Rows")
        output.extend(json.dumps(to_primitive(row), ensure_ascii=False, sort_keys=True) for row in rows)
    return "\n".join(output)

def render_data_protocol_payload(title: str, payload: dict[str, object]) -> str:
    from kairospy.surface.cli.output import render_key_value_panel, render_status_table

    protocols = payload.get("protocols")
    if isinstance(protocols, list):
        rows = [
            {
                "kind": item.get("kind", ""),
                "interface": item.get("interface", ""),
                "used_by": item.get("used_by", ""),
            }
            for item in protocols
            if isinstance(item, dict)
        ]
        return render_status_table(title, rows, columns=("kind", "interface", "used_by"))

    output = [render_key_value_panel(title, (
        ("Kind", payload.get("kind", "-")),
        ("Status", payload.get("status", "-")),
        ("Source", payload.get("source", payload.get("file", "-"))),
        ("Rows", payload.get("row_count", "-")),
        ("Next Command", payload.get("next_command", "-")),
    ))]
    checks = payload.get("checks")
    if isinstance(checks, list) and checks:
        rows = []
        for item in checks:
            if isinstance(item, dict):
                rows.append({
                    "name": item.get("name", ""),
                    "passed": item.get("passed", ""),
                    "value": item.get("value", ""),
                })
        output.append(render_status_table("Checks", rows, columns=("name", "passed", "value")))
    template = payload.get("template")
    if isinstance(template, str) and template:
        output.append(template.rstrip())
    issues = payload.get("issues")
    if isinstance(issues, list) and issues:
        rows = []
        for item in issues:
            if isinstance(item, dict):
                rows.append({
                    "code": item.get("code", ""),
                    "message": item.get("message", ""),
                })
        output.append(render_status_table("Issues", rows, columns=("code", "message")))
    return "\n\n".join(output)

def render_acquisition_plan_payload(title: str, payload: dict[str, object]) -> str:
    from kairospy.surface.cli.output import render_key_value_panel, render_status_table

    estimate = payload.get("estimate") if isinstance(payload.get("estimate"), dict) else {}
    selected = payload.get("selected") if isinstance(payload.get("selected"), dict) else {}
    requested = payload.get("requested") if isinstance(payload.get("requested"), dict) else {}
    missing = payload.get("missing") if isinstance(payload.get("missing"), list) else []
    rows = (
        ("Dataset", payload.get("logical_key", "-")),
        ("Provider", selected.get("provider", "-") if selected else "-"),
        ("Venue", selected.get("venue", "-") if selected else "-"),
        ("Provider Access", "available" if payload.get("connector_available") else "unavailable"),
        ("Complete", payload.get("complete", False)),
        ("Missing Ranges", len(missing)),
        ("Estimated Requests", estimate.get("requests", "-") if estimate else "-"),
        ("Estimated Instruments", estimate.get("instruments", "-") if estimate else "-"),
        ("Cost Class", estimate.get("cost_class", "-") if estimate else "-"),
    )
    output = [render_key_value_panel(title, rows)]
    tasks = payload.get("provider_tasks")
    if isinstance(tasks, dict) and tasks:
        task_rows = (
            ("Provider", tasks.get("provider", "-")),
            ("Task Type", tasks.get("task_type", "-")),
            ("Universe", tasks.get("universe", "-")),
            ("Symbols", tasks.get("symbols", "-")),
            ("Total Tasks", tasks.get("total_tasks", "-")),
            ("Cached Tasks", tasks.get("cached_tasks", "-")),
            ("Uncached Tasks", tasks.get("uncached_tasks", "-")),
            ("Resume Supported", tasks.get("resume_supported", "-")),
        )
        output.append(render_key_value_panel("Provider Task Plan", task_rows))
        ranges = tasks.get("ranges")
        if isinstance(ranges, list) and ranges:
            output.append(render_status_table(
                "Task Ranges",
                [item for item in ranges if isinstance(item, dict)],
                columns=("start", "end", "tasks", "cached", "uncached"),
            ))
        matrix = tasks.get("matrix")
        if isinstance(matrix, list) and matrix:
            output.append(render_status_table(
                "Task Matrix",
                [item for item in matrix if isinstance(item, dict)],
                columns=("year", "month", "tasks", "cached_monthly", "cached_daily_files"),
            ))
    elif isinstance(requested, dict):
        output.append(render_key_value_panel("Requested Window", (
            ("Start", requested.get("start", "-")),
            ("End", requested.get("end", "-")),
        )))
    return "\n\n".join(item for item in output if item)

# Backward-compatible private aliases for tests and legacy imports.
_emit_data_payload = emit_data_payload
_hide_default_data_internals = hide_default_data_internals
_render_data_use_payload = render_data_use_payload
_render_sample_payload = render_sample_payload
_diagnostic_rows = diagnostic_rows
_render_data_doctor_payload = render_data_doctor_payload
_dataset_acquire_payload = dataset_acquire_payload
_dataset_ready_status = dataset_ready_status
_ready_for_dataset_status = ready_for_dataset_status
_render_dataset_acquire_payload = render_dataset_acquire_payload
_render_data_product_doctor_payload = render_data_product_doctor_payload
_render_query_payload = render_query_payload
_render_replay_payload = render_replay_payload
_render_data_protocol_payload = render_data_protocol_payload
_render_acquisition_plan_payload = render_acquisition_plan_payload
