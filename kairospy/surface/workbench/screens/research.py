"""Dataset preparation and Research trust-gate workflows."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from rich.pretty import Pretty
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Label, OptionList, RichLog
from textual.worker import Worker

from kairospy.application.data import DataApplication, DataRequirement
from kairospy.application.research import ResearchApplication
from kairospy.research import ResearchSpec

from ..dialogs import ConfirmDialog, InputDialog
from ..widgets import ActionItem, ActionList, WorkspaceHeader


RESEARCH_ACTIONS = (
    ActionItem("datasets", "浏览 Datasets", "查看本地数据及质量信息", "1"),
    ActionItem("inspect", "查看 Dataset", "按 Dataset ID 和版本查看详情", "i"),
    ActionItem("plan-data", "审阅数据需求", "从 requirements.json 生成确定性计划", "2"),
    ActionItem("execute-data", "执行数据需求", "获取并发布已审阅的数据计划", "3"),
    ActionItem("execution", "查看数据执行", "按 plan hash 查看步骤与结果", "4"),
    ActionItem("sets", "Dataset Sets", "查看别名与不可变组合", "5"),
    ActionItem("set", "查看 Dataset Set", "按别名查看不可变数据组合", "s"),
    ActionItem("data-gate", "查看 Data Gate", "检查数据可信门禁", "6"),
    ActionItem("lock-plan", "锁定 Research Plan", "在查看 Holdout 前固定研究计划", "7"),
    ActionItem("show-plan", "查看 Research Plan", "按 plan hash 查看锁定内容", "8"),
    ActionItem("publish-gate", "发布 Research Gate", "校验证据并发布研究门禁", "9"),
    ActionItem("show-gate", "查看 Research Gate", "按 plan hash 查看研究证据", "0"),
)


class ResearchScreen(Screen[None]):
    TITLE = "Kairos"
    SUB_TITLE = "首页 › 数据研究"
    BINDINGS = [Binding("escape", "back", "返回")]

    def __init__(self) -> None:
        super().__init__()
        self._pending_path: str | None = None
        self._datasets: dict[str, Any] = {}

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label("准备数据研究", id="page-title")
        yield ActionList(*RESEARCH_ACTIONS, id="research-actions")
        yield Label("选择数据或研究操作。", id="research-status")
        yield DataTable(id="dataset-table", cursor_type="row", zebra_stripes=True)
        yield RichLog(id="research-result", wrap=True, highlight=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#dataset-table", DataTable).add_columns(
            "Dataset", "版本", "Owner", "类型", "标的", "事件", "质量"
        )
        self.query_one("#dataset-table", DataTable).display = False

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        action = event.option.id
        if action is None:
            return
        if action in {"datasets", "sets"}:
            self._run(action)
        elif action == "inspect":
            self.app.push_screen(
                InputDialog("Dataset ID"),
                lambda value: self._run(action, value) if value else None,
            )
        elif action == "set":
            self.app.push_screen(
                InputDialog("Dataset Set alias"),
                lambda value: self._run(action, value) if value else None,
            )
        elif action in {"plan-data", "execute-data"}:
            self.app.push_screen(
                InputDialog("requirements.json 路径", value="requirements.json"),
                lambda value: (
                    self._confirm_data_action(action, value) if value else None
                ),
            )
        elif action in {"execution", "data-gate", "show-plan", "show-gate"}:
            label = "composition hash" if action == "data-gate" else "plan hash"
            self.app.push_screen(
                InputDialog(f"输入 {label}"),
                lambda value: self._run(action, value) if value else None,
            )
        elif action == "lock-plan":
            self.app.push_screen(
                InputDialog("research-plan.json 路径", value="research-plan.json"),
                lambda value: self._run(action, value) if value else None,
            )
        elif action == "publish-gate":
            self.app.push_screen(
                InputDialog("research-plan.json 路径", value="research-plan.json"),
                self._publish_plan_selected,
            )

    def _confirm_data_action(self, action: str, value: str) -> None:
        if action != "execute-data":
            self._run(action, value)
            return
        self.app.push_screen(
            ConfirmDialog(
                "执行数据计划", f"确认执行 {value} 中的数据需求？", confirm_label="执行"
            ),
            lambda confirmed: self._run(action, value) if confirmed else None,
        )

    def _publish_plan_selected(self, value: str | None) -> None:
        if value is None:
            return
        self._pending_path = value
        self.app.push_screen(
            InputDialog("research-evidence.json 路径", value="research-evidence.json"),
            self._publish_evidence_selected,
        )

    def _publish_evidence_selected(self, value: str | None) -> None:
        if value is None:
            return
        self.app.push_screen(
            ConfirmDialog(
                "发布 Research Gate",
                "发布后将形成持久研究证据。",
                confirm_label="发布",
            ),
            lambda confirmed: (
                self._run("publish-gate", self._pending_path, value)
                if confirmed
                else None
            ),
        )

    def _run(
        self, action: str, value: str | None = None, extra: str | None = None
    ) -> None:
        state = self.app.state  # type: ignore[attr-defined]
        if action in {"execute-data", "lock-plan", "publish-gate"} and (
            state.dry_run or state.no_exec
        ):
            self._show(
                {"status": "preview", "action": action, "input": value, "extra": extra}
            )
            return
        self.query_one("#research-status", Label).update(f"正在执行：{action}…")
        self.run_worker(
            lambda: self._execute(action, value, extra),
            name=f"research-{action}",
            group="research-action",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _execute(self, action: str, value: str | None, extra: str | None) -> Any:
        owner = self.app.state.owner  # type: ignore[attr-defined]
        data = DataApplication(owner)
        if action == "datasets":
            return data.list()
        if action == "inspect":
            return data.describe(value or "")
        if action in {"plan-data", "execute-data"}:
            requirements = _requirements(Path(value or ""))
            plan = asyncio.run(data.plan(requirements))
            if action == "plan-data":
                return plan.as_dict()
            result = asyncio.run(data.execute(plan))
            return {
                "plan_hash": plan.plan_hash,
                "dataset_set": result.as_dict(),
                "execution": data.execution(plan.plan_hash),
            }
        if action == "execution":
            return data.execution(value or "")
        if action == "sets":
            return {"aliases": dict(data.set_aliases())}
        if action == "set":
            return data.load_set(value or "")
        if action == "data-gate":
            return data.trust_report(value or "")
        research = ResearchApplication(owner)
        if action == "lock-plan":
            return research.pin_plan(_research_spec(Path(value or "")))
        if action == "show-plan":
            return research.plan(value or "")
        if action == "show-gate":
            return research.gate_report(value or "")
        if action == "publish-gate":
            evidence = _object(Path(extra or ""), "Research evidence")
            results = evidence.get("results")
            limitations = evidence.get("limitations")
            if not isinstance(results, Mapping) or not isinstance(
                limitations, (list, tuple)
            ):
                raise ValueError("Research evidence requires results and limitations")
            return research.publish_gate(
                _research_spec(Path(value or "")),
                results={str(name): dict(item) for name, item in results.items()},
                conclusion=str(evidence.get("conclusion", "")),
                limitations=tuple(str(item) for item in limitations),
            )
        raise RuntimeError(f"unknown research action: {action}")

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "research-action":
            return
        if event.state.name == "ERROR":
            self.query_one("#research-status", Label).update(
                f"操作失败：{event.worker.error}"
            )
        elif event.state.name == "SUCCESS":
            result = event.worker.result
            if isinstance(result, tuple) and (
                not result or hasattr(result[0], "dataset_id")
            ):
                self._show_datasets(result)
            else:
                self.query_one("#research-status", Label).update("操作完成")
                if event.worker.name in {"research-inspect", "research-set"}:
                    self.app.push_screen(
                        ResearchResultScreen(
                            "Dataset 详情"
                            if event.worker.name == "research-inspect"
                            else "Dataset Set 详情",
                            result,
                        )
                    )
                else:
                    self._show(result)

    def _show_datasets(self, records: tuple[Any, ...]) -> None:
        table = self.query_one("#dataset-table", DataTable)
        table.display = True
        table.clear()
        self._datasets = {str(record.dataset_id): record for record in records}
        for record in records:
            table.add_row(
                record.dataset_id,
                record.version,
                record.owner,
                record.kind,
                record.subject,
                str(record.event_count),
                record.quality_status,
                key=str(record.dataset_id),
            )
        self.query_one("#research-status", Label).update(
            "尚无 Dataset。" if not records else f"共 {len(records)} 个 Dataset"
        )
        if records:
            table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        record = self._datasets.get(str(event.row_key.value))
        if record is None:
            return
        self._run("inspect", str(record.dataset_id))

    def _show(self, value: Any) -> None:
        log = self.query_one("#research-result", RichLog)
        log.clear()
        log.write(Pretty(value, expand_all=True))


class ResearchResultScreen(Screen[None]):
    """Keep inspected Data/Research values in the same navigation stack."""

    TITLE = "Kairos"
    BINDINGS = [Binding("escape", "back", "返回")]

    def __init__(self, label: str, value: Any) -> None:
        super().__init__()
        self.label = label
        self.value = value
        self.sub_title = f"首页 › 数据研究 › {label}"

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label(self.label, id="page-title")
        yield RichLog(id="research-detail", wrap=True, highlight=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#research-detail", RichLog).write(
            Pretty(self.value, expand_all=True)
        )

    def action_back(self) -> None:
        self.app.pop_screen()


def _requirements(path: Path) -> tuple[DataRequirement, ...]:
    value = json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))
    rows = value.get("requirements") if isinstance(value, Mapping) else value
    if not isinstance(rows, list) or not rows:
        raise ValueError("requirements file must contain a non-empty array or object")
    return tuple(DataRequirement(**dict(row)) for row in rows)


def _object(path: Path, description: str) -> Mapping[str, Any]:
    value = json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"{description} must contain a JSON object")
    return value


def _research_spec(path: Path) -> ResearchSpec:
    return ResearchSpec.from_dict(_object(path, "Research plan"))
