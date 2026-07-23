from __future__ import annotations

import json
import locale
import os
from typing import Iterable, Mapping, Sequence
import unicodedata


SUPPORTED_LANGUAGES = ("zh-CN", "en-US")


_TEXT = {
    "zh-CN": {
        "run.inspect": "运行详情",
        "section.data": "数据",
        "section.result": "结果",
        "section.validation": "验证",
        "section.files": "产物",
        "section.audit": "审计",
        "section.releases": "策略版本",
        "section.strategy": "代码",
        "section.explanation": "说明",
        "section.next": "下一步",
        "pass": "通过",
        "fail": "未通过",
        "created": "已创建",
        "reused": "已存在，继续使用",
        "error.title": "命令执行失败",
        "error.help": "请检查输入参数，或使用 --help 查看示例。",
    },
    "en-US": {
        "run.inspect": "Run details",
        "section.data": "Data",
        "section.result": "Result",
        "section.validation": "Validation",
        "section.files": "Artifacts",
        "section.audit": "Audit",
        "section.releases": "Strategy Releases",
        "section.strategy": "Code",
        "section.explanation": "Explanation",
        "section.next": "Next step",
        "pass": "passed",
        "fail": "failed",
        "created": "created",
        "reused": "already exists; reusing it",
        "error.title": "Command failed",
        "error.help": "Check the inputs or use --help for command examples.",
    },
}


_LABELS = {
    "zh-CN": {
        "version": "版本", "status": "状态", "hypothesis": "假设",
        "dataset": "数据集", "input_release": "数据版本", "input_identity": "数据身份",
        "input_hash": "内容指纹", "primary_time": "主时间字段", "start": "开始时间", "end": "结束时间",
        "bars": "K 线数量", "ready": "可用因子数量", "batch_replay_equal": "批量/事件一致性",
        "factor_hash": "因子结果指纹", "factor_id": "因子 ID", "factor_spec_hash": "因子定义指纹",
        "strategy_id": "策略 ID", "strategy_spec_hash": "策略定义指纹", "execution_policy_id": "执行策略",
        "mode": "运行模式", "trades": "交易数量", "orders": "订单数量", "fills": "成交数量",
        "market_data_source": "行情来源",
        "submitted_orders": "已提交订单", "hypothetical_intents": "假设交易意图",
        "final_equity": "期末权益", "final_cash": "期末现金", "final_position": "期末持仓",
        "restart_ready": "重启恢复", "passed": "验证结果", "count": "数量", "active_version": "当前版本",
        "created": "工作区", "root": "工作目录", "range": "数据区间", "lesson": "本步目标",
        "workspace": "工作区文件", "directory": "版本目录", "artifact": "运行产物",
        "script": "脚本", "shown": "显示行数", "total": "总行数", "rows": "数据行数", "columns": "字段数量",
        "missing_values": "缺失值", "duplicate_primary_times": "重复主时间",
        "chronological": "时间有序", "valid_ohlc": "OHLC 合法", "point_in_time_safe": "时点安全",
        "fields": "字段",
        "runtime_database": "运行数据库", "capture": "行情记录", "candidate_hash": "候选版本指纹",
        "audit_hash": "审计指纹", "decision_hash": "决策指纹", "intent_hash": "交易意图指纹",
        "comparisons": "一致性检查", "record": "审计记录", "tutorial": "教程",
        "gate_passed": "晋级门禁", "next_promotion": "下一晋级阶段", "evidence_bundle": "证据包",
        "current_status": "当前阶段", "target_status": "目标阶段",
        "transition_valid": "阶段顺序", "transition_reason": "阶段原因", "would_promote": "可晋级",
    },
    "en-US": {
        "version": "Version", "status": "Status", "hypothesis": "Hypothesis",
        "dataset": "Dataset", "input_release": "Dataset release", "input_identity": "Input identity",
        "input_hash": "Content hash", "primary_time": "Primary time", "start": "Start", "end": "End",
        "bars": "Bars", "ready": "Ready factors", "batch_replay_equal": "Batch/event parity",
        "factor_hash": "Factor result hash", "factor_id": "Factor ID", "factor_spec_hash": "Factor spec hash",
        "strategy_id": "Strategy ID", "strategy_spec_hash": "Strategy spec hash", "execution_policy_id": "Execution policy",
        "mode": "Run mode", "trades": "Trades", "orders": "Orders", "fills": "Fills",
        "market_data_source": "Market data source",
        "submitted_orders": "Submitted orders", "hypothetical_intents": "Hypothetical intents",
        "final_equity": "Final equity", "final_cash": "Final cash", "final_position": "Final position",
        "restart_ready": "Restart recovery", "passed": "Validation", "count": "Count", "active_version": "Active version",
        "created": "Workspace", "root": "Working directory", "range": "Data range", "lesson": "Lesson",
        "workspace": "Workspace file", "directory": "Release directory", "artifact": "Run artifact",
        "script": "Script", "shown": "Rows shown", "total": "Total rows", "rows": "Rows", "columns": "Columns",
        "missing_values": "Missing values", "duplicate_primary_times": "Duplicate primary times",
        "chronological": "Chronological", "valid_ohlc": "Valid OHLC", "point_in_time_safe": "Point-in-time safe",
        "fields": "Fields",
        "runtime_database": "Runtime database", "capture": "Market capture", "candidate_hash": "Candidate hash",
        "audit_hash": "Audit hash", "decision_hash": "Decision hash", "intent_hash": "Intent hash",
        "comparisons": "Parity checks", "record": "Audit record", "tutorial": "Tutorial",
        "gate_passed": "Promotion gate", "next_promotion": "Next promotion", "evidence_bundle": "Evidence bundle",
        "current_status": "Current status", "target_status": "Target status",
        "transition_valid": "Lifecycle transition", "transition_reason": "Transition reason",
        "would_promote": "Would promote",
    },
}


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


def _render_run_start_result(payload: Mapping[str, object], language: str) -> str:
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


def _render_run_config_result(payload: Mapping[str, object], language: str) -> str:
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


def _render_run_live_result(payload: Mapping[str, object], language: str) -> str:
    action = str(payload.get("live_action") or "status")
    if action in {"status", "attach"}:
        return _render_run_live_status(payload, language)
    if "operator_command" in payload:
        return _render_run_live_command(payload, language)
    return _render_run_live_summary(payload, language)


def _render_run_live_status(payload: Mapping[str, object], language: str) -> str:
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


def _render_run_live_command(payload: Mapping[str, object], language: str) -> str:
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


def _render_run_live_summary(payload: Mapping[str, object], language: str) -> str:
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


def _display_status_cell(value: object) -> str:
    if value is None:
        return "-"
    text = str(value)
    normalized = text.lower()
    if normalized == "ok":
        return "OK"
    if normalized in {"warning", "warn"}:
        return "WARN"
    if normalized == "error":
        return "ERROR"
    if normalized == "locked":
        return "LOCKED"
    return text


def _passed(payload: Mapping[str, object]) -> bool:
    if "passed" in payload:
        return bool(payload["passed"])
    if "batch_replay_equal" in payload:
        return bool(payload["batch_replay_equal"])
    return True


def _display(value: object, language: str) -> str:
    yes, no = (("是", "否") if language == "zh-CN" else ("yes", "no"))
    if isinstance(value, bool):
        return f"✓ {yes}" if value else f"✗ {no}"
    if isinstance(value, dict):
        if set(value) >= {"start", "end"}:
            return f"[{value['start']}, {value['end']})"
        return ", ".join(f"{key}={_display(item, language)}" for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return ", ".join(_display(item, language) for item in value)
    if value is None:
        return "—"
    return str(value)


def _section(title: str, rows: list[tuple[str, str]]) -> list[str]:
    width = max(_display_width(label) for label, _ in rows)
    return [title, *(f"  {_pad(label, width)}  {value}" for label, value in rows), ""]


def _table(title: str, headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> list[str]:
    if not rows:
        return []
    widths = [max(_display_width(str(value)) for value in (headers[index], *(row[index] for row in rows)))
              for index in range(len(headers))]
    output = [title, "  " + "  ".join(_pad(value, widths[index]) for index, value in enumerate(headers)),
              "  " + "  ".join("─" * width for width in widths)]
    output.extend("  " + "  ".join(_pad(value, widths[index]) for index, value in enumerate(row)) for row in rows)
    return [*output, ""]


def _paragraph(title: str, value: str) -> list[str]:
    return [title, *(f"  {line}" for line in value.splitlines()), ""]


def _display_width(value: str) -> int:
    return sum(2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1 for character in value)


def _pad(value: str, width: int) -> str:
    return value + " " * max(0, width - _display_width(value))


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
