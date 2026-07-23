from __future__ import annotations

from typing import Mapping

from kairospy.surface.cli.rendering.text import (
    LABELS as _LABELS,
    TEXT as _TEXT,
    display as _display,
    paragraph as _paragraph,
    section as _section,
    table as _table,
)


def render_run_start_result(payload: Mapping[str, object], language: str) -> str:
    messages, labels = _TEXT[language], _LABELS[language]
    summary = _run_start_summary(payload)
    status = _display(summary.get("status", "completed"), language)
    rows = [
        ("run_id", "run_id", summary.get("run_id")),
        ("mode", "mode", summary.get("mode")),
        ("status", "status", status),
        ("workspace", "workspace", summary.get("workspace")),
        ("decisions_count", "decisions_count", summary.get("decisions_count")),
    ]
    start_labels = {
        "zh-CN": {
            "workspace": "工作区",
            "decisions_count": "决策数量",
        },
        "en-US": {
            "workspace": "Workspace",
            "decisions_count": "Decisions",
        },
    }[language]
    lines = ["run finished", ""]
    lines.extend(_section(messages["section.result"], [
        (start_labels.get(alias, labels.get(label_key, label_key)), value)
        for alias, label_key, value in rows
        if value not in (None, "")
    ]))

    run_id = str(summary.get("run_id") or "")
    manifest = str(summary.get("manifest") or "")
    run_workspace = str(summary.get("run_workspace") or "")
    detail_commands = []
    if run_id:
        detail_commands.append(f"kairospy run inspect --run-id {run_id}")
        detail_commands.append(f"kairospy --format json run inspect --run-id {run_id}")
    if manifest:
        detail_commands.append(f"cat {manifest}")
    elif run_workspace:
        detail_commands.append(f"ls {run_workspace}")
    lines.extend(_paragraph(messages["section.next"], "\n".join(detail_commands)))
    return "\n".join(lines).rstrip()

def _run_start_summary(payload: Mapping[str, object]) -> dict[str, object]:
    workspace = payload.get("workspace")
    workspace_name = workspace.get("name") if isinstance(workspace, Mapping) else workspace
    artifacts = payload.get("artifacts")
    summary = artifacts.get("summary") if isinstance(artifacts, Mapping) else None
    return {
        "run_id": payload.get("run_id"),
        "mode": payload.get("mode"),
        "status": payload.get("status"),
        "workspace": workspace_name,
        "decisions_count": _decisions_count(payload),
        "manifest": payload.get("manifest"),
        "run_workspace": payload.get("run_workspace"),
        "summary": summary,
    }

def _decisions_count(payload: Mapping[str, object]) -> object:
    if "decisions_count" in payload:
        return payload["decisions_count"]
    return None

def render_run_config_result(payload: Mapping[str, object], language: str) -> str:
    text = {
        "zh-CN": {
            "title": "运行配置检查",
            "summary": "配置摘要",
            "entry": "运行入口",
            "params": "参数",
            "bindings": "资源绑定",
            "guards": "安全开关",
            "issues": "问题",
            "next": "下一步",
            "file": "文件",
            "valid": "可启动",
            "name": "名称",
            "mode": "模式",
            "workspace": "工作区代码",
            "strategy": "策略代码",
            "account": "账户",
            "market": "行情",
            "execution": "执行",
            "provider": "行情/交易供应商",
            "driver": "执行驱动",
            "key": "项目",
            "value": "值",
            "status": "状态",
            "enabled": "开启",
            "disabled": "关闭",
            "none": "无",
            "ok": "配置有效，可以启动。",
            "fix": "先修复上面的问题，再启动。",
            "start": "启动: kairospy run live start --run-id <run-id> --config {path} --confirm-live\n控制台: kairospy run live attach --run-id <run-id>",
        },
        "en-US": {
            "title": "Run Config Check",
            "summary": "Summary",
            "entry": "Run Entry",
            "params": "Parameters",
            "bindings": "Bindings",
            "guards": "Safety Switches",
            "issues": "Issues",
            "next": "Next Step",
            "file": "File",
            "valid": "Startable",
            "name": "Name",
            "mode": "Mode",
            "workspace": "Workspace code",
            "strategy": "Strategy code",
            "account": "Account",
            "market": "Market",
            "execution": "Execution",
            "provider": "Provider",
            "driver": "Execution driver",
            "key": "Item",
            "value": "Value",
            "status": "Status",
            "enabled": "enabled",
            "disabled": "disabled",
            "none": "none",
            "ok": "Config is valid and can be started.",
            "fix": "Fix the issues above before starting.",
            "start": "Start: kairospy run live start --run-id <run-id> --config {path} --confirm-live\nConsole: kairospy run live attach --run-id <run-id>",
        },
    }[language]
    run = payload.get("run") if isinstance(payload.get("run"), Mapping) else {}
    bindings = payload.get("bindings") if isinstance(payload.get("bindings"), Mapping) else {}
    live = payload.get("live") if isinstance(payload.get("live"), Mapping) else {}
    params = payload.get("params") if isinstance(payload.get("params"), Mapping) else {}
    guards = payload.get("guards") if isinstance(payload.get("guards"), Mapping) else {}
    issues = payload.get("issues")
    issue_list = list(issues) if isinstance(issues, (list, tuple)) else []
    path = str(payload.get("path") or "")
    lines = [text["title"], ""]
    lines.extend(_section(text["summary"], [
        (text["file"], path or "—"),
        (text["valid"], _display(bool(payload.get("valid")), language)),
        (text["name"], str(run.get("name") or "—")),
        (text["mode"], _human_status(run.get("mode"), language)),
    ]))
    lines.extend(_table(text["entry"], (text["key"], text["value"]), [
        (text["workspace"], str(run.get("workspace") or "—")),
        (text["strategy"], str(run.get("strategy") or "—")),
    ]))
    binding_rows = [
        (text["account"], str(bindings.get("account") or "—")),
        (text["market"], _display(bindings.get("market") or (), language) or "—"),
        (text["execution"], str(bindings.get("execution") or "—")),
    ]
    if live:
        binding_rows.extend([
            (text["provider"], str(live.get("provider") or "—")),
            (text["driver"], str(live.get("execution_driver") or "—")),
        ])
    lines.extend(_table(text["bindings"], (text["key"], text["value"]), binding_rows))
    if params:
        lines.extend(_table(
            text["params"],
            (text["key"], text["value"]),
            [(str(key), _display(value, language)) for key, value in sorted(params.items())],
        ))
    if guards:
        lines.extend(_table(
            text["guards"],
            (text["key"], text["status"]),
            [(str(key), text["enabled"] if bool(value) else text["disabled"]) for key, value in sorted(guards.items())],
        ))
    if issue_list:
        lines.extend(_table(text["issues"], (text["key"], text["value"]), [
            (str(index), str(issue)) for index, issue in enumerate(issue_list, start=1)
        ]))
        lines.extend(_paragraph(text["next"], text["fix"]))
    else:
        next_line = text["ok"]
        if str(run.get("mode") or "") == "live" and path:
            next_line += "\n" + text["start"].format(path=path)
        lines.extend(_paragraph(text["next"], next_line))
    return "\n".join(lines).rstrip()

def render_run_live_result(payload: Mapping[str, object], language: str) -> str:
    action = str(payload.get("live_action") or "status")
    if action in {"status", "attach"}:
        return render_run_live_status(payload, language)
    if "operator_command" in payload:
        return render_run_live_command(payload, language)
    return render_run_live_summary(payload, language)

def render_run_live_status(payload: Mapping[str, object], language: str) -> str:
    text = {
        "zh-CN": {
            "title": "实时运行状态",
            "summary": "摘要",
            "operations": "操作状态",
            "target": "目标仓位",
            "services": "服务",
            "incidents": "事故",
            "next": "排障",
            "run": "运行",
            "status": "状态",
            "phase": "阶段",
            "health": "健康",
            "reason": "原因",
            "stop_requested": "停止请求",
            "pending_commands": "待处理命令",
            "open_incidents": "未关闭事故",
            "unresolved_orders": "待恢复订单",
            "outbox_backlog": "订单队列积压",
            "market_data": "行情新鲜度",
            "execution": "执行",
            "updated": "更新时间",
            "no_services": "未报告服务",
            "no_incidents": "无未关闭事故",
            "not_set": "未设置",
            "diagnostics": "完整诊断数据: kairospy --format json run live status --run-id {run_id}",
        },
        "en-US": {
            "title": "Live Run Status",
            "summary": "Summary",
            "operations": "Operations",
            "target": "Target Position",
            "services": "Services",
            "incidents": "Incidents",
            "next": "Diagnostics",
            "run": "Run",
            "status": "Status",
            "phase": "Phase",
            "health": "Health",
            "reason": "Reason",
            "stop_requested": "Stop requested",
            "pending_commands": "Pending commands",
            "open_incidents": "Open incidents",
            "unresolved_orders": "Unresolved orders",
            "outbox_backlog": "Order queue backlog",
            "market_data": "Market data freshness",
            "execution": "Execution",
            "updated": "Updated",
            "no_services": "No services reported",
            "no_incidents": "No open incidents",
            "not_set": "Not set",
            "diagnostics": "Full diagnostics: kairospy --format json run live status --run-id {run_id}",
        },
    }[language]
    run_id = str(payload.get("run_id") or "")
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), Mapping) else {}
    health = payload.get("health") if isinstance(payload.get("health"), Mapping) else {}
    target = payload.get("target_position") if isinstance(payload.get("target_position"), Mapping) else {}
    rows = [
        (text["run"], run_id or "—"),
        (text["status"], _human_status(payload.get("status"), language)),
        (text["phase"], _human_status(payload.get("phase"), language)),
        (text["health"], _human_health(health, language)),
    ]
    if payload.get("reason"):
        rows.append((text["reason"], _human_status(payload.get("reason"), language)))
    if payload.get("stop_requested") is not None:
        rows.append((text["stop_requested"], _display(bool(payload.get("stop_requested")), language)))

    lines = [text["title"], ""]
    lines.extend(_section(text["summary"], [(label, str(value)) for label, value in rows]))
    lines.extend(_section(text["operations"], [
        (text["pending_commands"], str(metrics.get("operator_command_backlog", 0))),
        (text["open_incidents"], str(payload.get("open_incident_count", metrics.get("open_incident_count", 0)) or 0)),
        (text["unresolved_orders"], str(metrics.get("unresolved_order_count", 0))),
        (text["outbox_backlog"], str(metrics.get("outbox_backlog_count", 0))),
        (text["market_data"], _human_status(metrics.get("market_freshness_status"), language)),
    ]))
    if target:
        lines.extend(_section(text["target"], [
            ("Status" if language == "en-US" else "状态", _human_status(target.get("status"), language)),
            ("Intent" if language == "en-US" else "意图", str(target.get("intent_id") or "—")),
            (text["execution"], _human_status(target.get("execution_status"), language)),
            ("Legs" if language == "en-US" else "明细", _format_target_legs(target.get("legs"), language)),
            (text["updated"], str(target.get("updated_at") or "—")),
        ]))
    services = _service_rows(payload.get("services"), language)
    if services:
        headers = (("服务", "状态", "重启") if language == "zh-CN" else ("Service", "Status", "Restarts"))
        lines.extend(_table(text["services"], headers, services))
    else:
        lines.extend(_paragraph(text["services"], text["no_services"]))
    incidents = _incident_rows(payload.get("incidents"), language)
    if incidents:
        headers = (("级别", "状态", "标题") if language == "zh-CN" else ("Severity", "Status", "Title"))
        lines.extend(_table(text["incidents"], headers, incidents))
    else:
        lines.extend(_paragraph(text["incidents"], text["no_incidents"]))
    if run_id:
        lines.extend(_paragraph(text["next"], text["diagnostics"].format(run_id=run_id)))
    return "\n".join(lines).rstrip()

def render_run_live_command(payload: Mapping[str, object], language: str) -> str:
    text = {
        "zh-CN": {
            "title": "实时运行命令",
            "summary": "结果",
            "run": "运行",
            "command": "命令",
            "status": "状态",
            "reason": "原因",
            "next": "下一步",
            "status_cmd": "查看状态: kairospy run live status --run-id {run_id}",
        },
        "en-US": {
            "title": "Live Run Command",
            "summary": "Result",
            "run": "Run",
            "command": "Command",
            "status": "Status",
            "reason": "Reason",
            "next": "Next Step",
            "status_cmd": "Check status: kairospy run live status --run-id {run_id}",
        },
    }[language]
    command = payload.get("operator_command") if isinstance(payload.get("operator_command"), Mapping) else {}
    run_id = str(payload.get("run_id") or command.get("run_id") or "")
    lines = [text["title"], ""]
    rows = [
        (text["run"], run_id or "—"),
        (text["command"], _human_status(command.get("command_type") or payload.get("live_action"), language)),
        (text["status"], _human_status(command.get("status") or payload.get("status"), language)),
    ]
    if command.get("reason") or payload.get("reason"):
        rows.append((text["reason"], str(command.get("reason") or payload.get("reason"))))
    lines.extend(_section(text["summary"], [(label, str(value)) for label, value in rows]))
    if run_id:
        lines.extend(_paragraph(text["next"], text["status_cmd"].format(run_id=run_id)))
    return "\n".join(lines).rstrip()

def render_run_live_summary(payload: Mapping[str, object], language: str) -> str:
    heading = "实时运行" if language == "zh-CN" else "Live Run"
    labels = {
        "run_id": "运行" if language == "zh-CN" else "Run",
        "live_action": "操作" if language == "zh-CN" else "Action",
        "status": "状态" if language == "zh-CN" else "Status",
        "phase": "阶段" if language == "zh-CN" else "Phase",
        "pid": "进程" if language == "zh-CN" else "Process",
    }
    rows = [
        (labels[key], _human_status(payload.get(key), language) if key in {"status", "phase", "live_action"} else _display(payload.get(key), language))
        for key in ("run_id", "live_action", "status", "phase", "pid")
        if payload.get(key) not in (None, "")
    ]
    return "\n".join([heading, "", *_section("结果" if language == "zh-CN" else "Result", rows)]).rstrip()

def _human_health(health: Mapping[str, object], language: str) -> str:
    status = _human_status(health.get("status"), language)
    reasons = health.get("reasons")
    if isinstance(reasons, (list, tuple)) and reasons:
        return f"{status} ({', '.join(str(item) for item in reasons[:3])})"
    return status

def _human_status(value: object, language: str) -> str:
    raw = str(value or "unknown")
    labels = {
        "zh-CN": {
            "accepted": "已接受",
            "command_submitted": "已提交",
            "created": "已创建",
            "failed": "失败",
            "inactive": "未运行",
            "not_started": "未启动",
            "not_submitted": "未提交",
            "ok": "正常",
            "open": "未关闭",
            "pending": "等待处理",
            "request_status_snapshot": "请求状态快照",
            "running": "运行中",
            "stale": "已失联",
            "stop": "停止",
            "stop_requested": "停止中",
            "stopped": "已停止",
            "target_position": "目标仓位",
            "unknown": "未知",
            "unknown_external_state": "外部状态未知",
        },
        "en-US": {
            "accepted": "accepted",
            "command_submitted": "submitted",
            "created": "created",
            "failed": "failed",
            "inactive": "inactive",
            "not_started": "not started",
            "not_submitted": "not submitted",
            "ok": "ok",
            "open": "open",
            "pending": "pending",
            "request_status_snapshot": "request status snapshot",
            "running": "running",
            "stale": "stale",
            "stop": "stop",
            "stop_requested": "stopping",
            "stopped": "stopped",
            "target_position": "target position",
            "unknown": "unknown",
            "unknown_external_state": "external state unknown",
        },
    }[language]
    return labels.get(raw, raw.replace("_", " "))

def _format_target_legs(value: object, language: str) -> str:
    side_labels = {
        "zh-CN": {"long": "做多", "short": "做空", "buy": "买入", "sell": "卖出"},
        "en-US": {"long": "long", "short": "short", "buy": "buy", "sell": "sell"},
    }[language]
    if not isinstance(value, (list, tuple)) or not value:
        return "—"
    rendered = []
    for item in value:
        if not isinstance(item, Mapping):
            rendered.append(str(item))
            continue
        side = side_labels.get(str(item.get("side") or ""), str(item.get("side") or ""))
        quantity = str(item.get("quantity") or "")
        instrument = str(item.get("instrument") or "")
        venue = str(item.get("venue") or "")
        rendered.append(" ".join(part for part in (side, quantity, instrument, f"@ {venue}" if venue else "") if part))
    return "; ".join(rendered)

def _service_rows(value: object, language: str) -> list[tuple[str, str, str]]:
    if not isinstance(value, (list, tuple)):
        return []
    rows = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        rows.append((
            str(item.get("name") or "service"),
            _human_status(item.get("status"), language),
            str(item.get("restart_count") or 0),
        ))
    return rows

def _incident_rows(value: object, language: str) -> list[tuple[str, str, str]]:
    severity_labels = {
        "zh-CN": {"warning": "警告", "error": "错误", "critical": "严重", "info": "信息"},
        "en-US": {"warning": "warning", "error": "error", "critical": "critical", "info": "info"},
    }[language]
    if not isinstance(value, (list, tuple)):
        return []
    rows = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        rows.append((
            severity_labels.get(str(item.get("severity") or "warning"), str(item.get("severity") or "warning")),
            _human_status(item.get("status") or "open", language),
            str(item.get("title") or item.get("incident_id") or "incident"),
        ))
    return rows

# Backward-compatible private aliases.
_render_run_start_result = render_run_start_result
_render_run_config_result = render_run_config_result
_render_run_live_result = render_run_live_result
_render_run_live_status = render_run_live_status
_render_run_live_command = render_run_live_command
_render_run_live_summary = render_run_live_summary
