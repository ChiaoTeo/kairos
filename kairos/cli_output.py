from __future__ import annotations

import json
import locale
import os
from typing import Iterable, Mapping, Sequence
import unicodedata


SUPPORTED_LANGUAGES = ("zh-CN", "en-US")


_TEXT = {
    "zh-CN": {
        "tutorial.sma": "SMA 研究教程已就绪",
        "study.create": "研究工作区已创建",
        "study.freeze": "研究候选版本已冻结",
        "study.inspect": "研究工作区详情",
        "study.data": "研究数据预览",
        "study.profile": "研究数据检查 {status}",
        "study.scaffold": "研究脚本已生成",
        "factor.register-sma": "SMA 因子版本已注册",
        "factor.verify-sma": "SMA 因子验证 {status}",
        "strategy.register-sma": "SMA 策略版本已注册",
        "strategy.register-builtins": "内置策略版本已注册",
        "strategy.register-btc-iron-condor": "BTC Iron Condor 策略候选已注册",
        "strategy.inspect": "策略版本详情",
        "strategy.status": "策略版本状态",
        "strategy.activate": "策略版本已激活",
        "strategy.rollback": "策略版本已回滚",
        "strategy.check-promotion": "策略晋级证据检查 {status}",
        "strategy.promote": "策略版本已晋级",
        "run.backtest": "策略回测完成",
        "run.simulate": "策略历史仿真完成",
        "run.paper": "策略 Paper Session 完成",
        "run.shadow": "策略 Shadow Session 完成",
        "run.inspect": "运行详情",
        "run.artifact-replay": "运行产物重放验证 {status}",
        "run.capture-replay": "行情捕获重放验证 {status}",
        "run.reference": "参考策略场景完成",
        "section.data": "数据",
        "section.result": "结果",
        "section.validation": "验证",
        "section.files": "产物",
        "section.audit": "审计",
        "section.releases": "策略版本",
        "section.explanation": "说明",
        "section.next": "下一步",
        "pass": "通过",
        "fail": "未通过",
        "created": "已创建",
        "reused": "已存在，继续使用",
        "warming": "慢均线需要 {slow} 根 K 线完成预热，前 {warmup} 根不会产生完整因子。",
        "replay_meaning": "批量研究与逐事件运行结果一致，可以继续；这不代表策略能够盈利。",
        "error.title": "命令执行失败",
        "error.help": "请检查输入参数，或使用 --help 查看示例。",
    },
    "en-US": {
        "tutorial.sma": "SMA study tutorial is ready",
        "study.create": "Study workspace created",
        "study.freeze": "Study candidate frozen",
        "study.inspect": "Study workspace details",
        "study.data": "Study data preview",
        "study.profile": "Study data profile {status}",
        "study.scaffold": "Study script generated",
        "factor.register-sma": "SMA Factor Release registered",
        "factor.verify-sma": "SMA factor validation {status}",
        "strategy.register-sma": "SMA Strategy Release registered",
        "strategy.register-builtins": "Built-in Strategy Releases registered",
        "strategy.register-btc-iron-condor": "BTC Iron Condor candidate registered",
        "strategy.inspect": "Strategy Release details",
        "strategy.status": "Strategy Release status",
        "strategy.activate": "Strategy Release activated",
        "strategy.rollback": "Strategy Release rolled back",
        "strategy.check-promotion": "Strategy promotion evidence check {status}",
        "strategy.promote": "Strategy Release promoted",
        "run.backtest": "Strategy backtest completed",
        "run.simulate": "Strategy historical simulation completed",
        "run.paper": "Strategy paper session completed",
        "run.shadow": "Strategy shadow session completed",
        "run.inspect": "Run details",
        "run.artifact-replay": "Run artifact replay validation {status}",
        "run.capture-replay": "Market capture replay validation {status}",
        "run.reference": "Reference strategy scenario completed",
        "section.data": "Data",
        "section.result": "Result",
        "section.validation": "Validation",
        "section.files": "Artifacts",
        "section.audit": "Audit",
        "section.releases": "Strategy Releases",
        "section.explanation": "Explanation",
        "section.next": "Next step",
        "pass": "passed",
        "fail": "failed",
        "created": "created",
        "reused": "already exists; reusing it",
        "warming": "The slow SMA needs {slow} bars to warm up; the first {warmup} bars cannot produce a complete factor.",
        "replay_meaning": "Batch study analysis and event replay agree. You may continue; this does not mean the strategy is profitable.",
        "error.title": "Command failed",
        "error.help": "Check the inputs or use --help for command examples.",
    },
}


_LABELS = {
    "zh-CN": {
        "study_id": "研究 ID", "version": "版本", "status": "状态", "hypothesis": "研究假设",
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
        "script": "研究脚本", "shown": "显示行数", "total": "总行数", "rows": "数据行数", "columns": "字段数量",
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
        "study_id": "Study ID", "version": "Version", "status": "Status", "hypothesis": "Hypothesis",
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
        "script": "Study script", "shown": "Rows shown", "total": "Total rows", "rows": "Rows", "columns": "Columns",
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
    raw = os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG")
    if not raw:
        raw = locale.getlocale()[0] or ""
    return "zh-CN" if raw.lower().startswith("zh") else "en-US"


def render_product_result(group: str, action: str, payload: Mapping[str, object], language: str) -> str:
    messages, labels = _TEXT[language], _LABELS[language]
    passed = _passed(payload)
    status = f"✓ {messages['pass']}" if passed else f"✗ {messages['fail']}"
    title = messages.get(f"{group}.{action}", f"{group} {action}").format(status=status)
    lines = [title, ""]

    if group == "tutorial":
        state = messages["created"] if payload.get("created") else messages["reused"]
        sections = (
            ("section.result", ("created", "study_id", "status", "hypothesis"), {"created": state}),
            ("section.data", ("dataset", "input_hash", "range"), {}),
            ("section.files", ("root", "workspace"), {}),
        )
    elif (group, action) == ("study", "data"):
        sections = (
            ("section.data", ("study_id", "version", "dataset", "shown", "total"), {}),
        )
    elif (group, action) == ("study", "profile"):
        sections = (
            ("section.data", ("study_id", "version", "dataset", "rows", "columns"), {}),
            ("section.validation", ("missing_values", "duplicate_primary_times", "chronological",
                                    "valid_ohlc", "point_in_time_safe", "passed"), {}),
        )
    elif (group, action) == ("study", "inspect"):
        sections = (
            ("section.result", ("study_id", "version", "status", "hypothesis"), {}),
            ("section.data", ("dataset", "input_hash", "primary_time", "start", "end", "rows", "columns", "fields"), {}),
        )
    elif (group, action) == ("study", "scaffold"):
        sections = (
            ("section.result", ("study_id", "version"), {}),
            ("section.files", ("script",), {}),
        )
    elif (group, action) == ("factor", "verify-sma"):
        sections = (
            ("section.data", ("input_identity", "bars", "ready"), {}),
            ("section.validation", ("batch_replay_equal", "factor_hash"), {}),
        )
    elif group == "run":
        sections = (
            ("section.result", ("mode", "market_data_source", "passed", "bars", "trades", "orders", "fills", "final_equity",
                                "final_cash", "final_position", "restart_ready", "hypothetical_intents",
                                "submitted_orders", "comparisons"), {}),
            ("section.data", ("input_identity", "capture"), {}),
            ("section.files", ("artifact", "runtime_database"), {}),
        )
    else:
        sections = (
            ("section.result", ("study_id", "factor_id", "strategy_id", "version", "status", "active_version",
                                "count", "factor_spec_hash", "strategy_spec_hash", "execution_policy_id",
                                "candidate_hash", "current_status", "target_status", "gate_passed",
                                "transition_valid", "transition_reason", "would_promote",
                                "next_promotion", "passed"), {}),
            ("section.data", ("input_release", "input_hash", "primary_time", "start", "end"), {}),
            ("section.audit", ("evidence_bundle",), {}),
            ("section.files", ("workspace", "directory", "record"), {}),
        )

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

    preview_rows = payload.get("rows")
    preview_columns = payload.get("columns")
    if (group, action) == ("study", "data") and isinstance(preview_rows, list) and isinstance(preview_columns, (list, tuple)):
        headers = tuple(str(item) for item in preview_columns)
        table_rows = [tuple(_display(row.get(column), language) for column in headers)
                      for row in preview_rows if isinstance(row, dict)]
        lines.extend(_table(messages["section.data"], headers, table_rows))
        shown.update(("rows", "columns"))

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
    console = Console(record=True, force_terminal=False, color_system=None, width=120)
    table = Table(title=title, show_header=True, header_style="bold")
    for column in columns:
        table.add_column(column.replace("_", " ").title())
    for row in rows:
        table.add_row(*(_display_status_cell(row.get(column, "")) for column in columns))
    console.print(table)
    return console.export_text().rstrip()


def _render_rich_key_value_panel(title: str, rows: Sequence[tuple[str, object]]) -> str | None:
    try:
        from rich.console import Console
        from rich.table import Table
    except Exception:
        return None
    console = Console(record=True, force_terminal=False, color_system=None, width=120)
    table = Table(title=title, show_header=False)
    table.add_column("Field")
    table.add_column("Value")
    for label, value in rows:
        table.add_row(label, _display_status_cell(value))
    console.print(table)
    return console.export_text().rstrip()


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
