from __future__ import annotations

from rich.console import Console
from rich.text import Text

from kairospy.surface.workbench.screens.presentation import (
    ResultTone,
    conclusion,
    count,
    duration_from_millis,
    duration_from_nanos,
    duration_from_seconds,
    facts,
    local_time_from_unix_nanos,
    next_steps,
    percentage,
    section,
)


def _plain(value: object, *, width: int = 80) -> str:
    console = Console(width=width, record=True)
    console.print(value)
    return console.export_text().strip()


def test_result_primitives_are_borderless_and_linearizable() -> None:
    rendered = section(
        "关键事实",
        facts((("服务状态", Text("正常", style="green")), ("控制连接", "可用"))),
    )

    text = _plain(rendered)

    assert "关键事实" in text
    assert "服务状态  正常" in text
    assert "控制连接  可用" in text
    assert "╭" not in text


def test_conclusion_and_next_steps_keep_textual_semantics() -> None:
    assert _plain(conclusion("结果未知", tone=ResultTone.WARNING)) == "结果未知"
    assert _plain(next_steps(("查询当前状态。", "确认后再继续。"))) == (
        "建议下一步\n1. 查询当前状态。\n2. 确认后再继续。"
    )


def test_mechanical_formatters_do_not_guess_zero_sample_or_units() -> None:
    assert count(13_903) == "13,903"
    assert percentage(1, 90) == "1.1%（1/90）"
    assert percentage(0, 0) == "暂无样本"
    assert duration_from_nanos(5_458_000) == "5.458 ms"
    assert duration_from_millis(2_500) == "2.5 秒"
    assert duration_from_seconds(90) == "1.5 分钟"


def test_unix_nanos_time_includes_date_and_offset() -> None:
    rendered = local_time_from_unix_nanos(0)

    assert rendered.startswith("1970-01-01")
    assert rendered[-5:].isdigit() or (
        rendered[-5] in {"+", "-"} and rendered[-4:].isdigit()
    )
