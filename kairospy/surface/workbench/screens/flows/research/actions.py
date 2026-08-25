"""Data and Research actions for the Workbench product slice."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from kairospy.research import ResearchSpec
from kairospy.research.apps.data.application import DataApplication, DataRequirement
from kairospy.research.apps.experiments.application import ResearchApplication

from ....widgets import ActionItem


DATA_ACTIONS = (
    ActionItem("datasets", "浏览 Datasets", "查看本地数据及质量信息", "1"),
    ActionItem("inspect", "查看 Dataset", "按 Dataset ID 和版本查看详情", "2"),
    ActionItem("plan-data", "审阅数据需求", "从 requirements.json 生成计划", "3"),
    ActionItem("execute-data", "执行数据需求", "获取并发布已审阅的数据计划", "4"),
    ActionItem("execution", "查看数据执行", "按 plan hash 查看步骤与结果", "5"),
    ActionItem("sets", "Dataset Sets", "查看别名与不可变组合", "6"),
    ActionItem("set", "查看 Dataset Set", "按别名查看不可变数据组合", "7"),
    ActionItem("data-gate", "查看 Data Gate", "检查数据可信门禁", "8"),
)

RESEARCH_ACTIONS = (
    ActionItem("lock-plan", "锁定 Research Plan", "在查看 Holdout 前固定计划", "1"),
    ActionItem("show-plan", "查看 Research Plan", "按 plan hash 查看锁定内容", "2"),
    ActionItem("publish-gate", "发布 Research Gate", "校验证据并发布研究门禁", "3"),
    ActionItem("show-gate", "查看 Research Gate", "按 plan hash 查看研究证据", "4"),
)


def execute(
    state: Any,
    action: str,
    value: str | None = None,
    extra: str | None = None,
) -> Any:
    owner = _owner(state)
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
        if extra and plan.plan_hash != extra:
            raise ValueError(
                f"data plan hash mismatch: expected={extra}, actual={plan.plan_hash}"
            )
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
    raise ValueError(f"unknown research action: {action}")


def preview(action: str, value: str | None, extra: str | None = None) -> dict[str, Any]:
    return {
        "status": "preview",
        "action": action,
        "input": value,
        "extra": extra,
    }


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


def _owner(state: Any) -> Any:
    if state.owner is None:
        raise RuntimeError(state.load_error or "当前没有可用的 workspace")
    return state.owner


__all__ = ["DATA_ACTIONS", "RESEARCH_ACTIONS", "execute", "preview"]
