"""Contextual help for the current workbench screen."""

from __future__ import annotations

from collections.abc import Iterable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, Static


class HelpDialog(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "关闭")]

    def __init__(
        self,
        context: str,
        bindings: Iterable[Binding],
        *,
        show_product_map: bool = False,
    ) -> None:
        super().__init__()
        self.context = context
        self.context_bindings = tuple(bindings)
        self.show_product_map = show_product_map

    def compose(self) -> ComposeResult:
        lines = [
            f"{binding.key:<12} {binding.description}"
            for binding in self.context_bindings
            if binding.show and binding.description
        ]
        if self.show_product_map:
            lines.extend(
                (
                    "",
                    "首页入口",
                    "1  市场行情     2  市场标的     3  策略与运行",
                    "4  运行资源     5  数据研究     6  系统维护",
                )
            )
        lines.extend(
            (
                "",
                "通用操作",
                "↑↓ 选择 · Enter 打开/提交 · Esc 返回/取消",
                "Ctrl+P 命令面板 · Ctrl+C 取消当前任务 · q 退出",
                "输入框聚焦时，数字、? 和 q 都作为普通文本输入。",
            )
        )
        with Vertical(classes="dialog"):
            yield Label(f"帮助 · {self.context}", classes="dialog-title")
            yield Static("\n".join(lines), id="help-content")

    def action_close(self) -> None:
        self.dismiss(None)


__all__ = ["HelpDialog"]
