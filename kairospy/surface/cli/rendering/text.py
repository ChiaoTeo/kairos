from __future__ import annotations

from typing import Mapping
import unicodedata


SUPPORTED_LANGUAGES = ("zh-CN", "en-US")


TEXT = {
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


LABELS = {
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


def display_status_cell(value: object) -> str:
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

def passed(payload: Mapping[str, object]) -> bool:
    if "passed" in payload:
        return bool(payload["passed"])
    if "batch_replay_equal" in payload:
        return bool(payload["batch_replay_equal"])
    return True

def display(value: object, language: str) -> str:
    yes, no = (("是", "否") if language == "zh-CN" else ("yes", "no"))
    if isinstance(value, bool):
        return f"✓ {yes}" if value else f"✗ {no}"
    if isinstance(value, dict):
        if set(value) >= {"start", "end"}:
            return f"[{value['start']}, {value['end']})"
        return ", ".join(f"{key}={display(item, language)}" for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return ", ".join(display(item, language) for item in value)
    if value is None:
        return "—"
    return str(value)

def section(title: str, rows: list[tuple[str, str]]) -> list[str]:
    width = max(display_width(label) for label, _ in rows)
    return [title, *(f"  {pad(label, width)}  {value}" for label, value in rows), ""]

def table(title: str, headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> list[str]:
    if not rows:
        return []
    widths = [max(display_width(str(value)) for value in (headers[index], *(row[index] for row in rows)))
              for index in range(len(headers))]
    output = [title, "  " + "  ".join(pad(value, widths[index]) for index, value in enumerate(headers)),
              "  " + "  ".join("─" * width for width in widths)]
    output.extend("  " + "  ".join(pad(value, widths[index]) for index, value in enumerate(row)) for row in rows)
    return [*output, ""]

def paragraph(title: str, value: str) -> list[str]:
    return [title, *(f"  {line}" for line in value.splitlines()), ""]

def display_width(value: str) -> int:
    return sum(2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1 for character in value)

def pad(value: str, width: int) -> str:
    return value + " " * max(0, width - display_width(value))

# Backward-compatible private aliases.
_display_status_cell = display_status_cell
_passed = passed
_display = display
_section = section
_table = table
_paragraph = paragraph
_display_width = display_width
_pad = pad
