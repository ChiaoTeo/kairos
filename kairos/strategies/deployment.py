from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from kairos.ports import Environment
from kairos.domain.strategy_contract import StrategyLifecycle


@dataclass(frozen=True,slots=True)
class DeploymentDecision:
    allowed: bool
    lifecycle: StrategyLifecycle|None
    reason: str
    strategy_directory: Path|None


class StrategyDeploymentGate:
    def __init__(self,root: str|Path="data/strategies") -> None:self.root=Path(root)
    def evaluate(self,strategy_id: str,environment: Environment,*,simulated_venue: bool=False) -> DeploymentDecision:
        directory=self.root/strategy_id
        versions=sorted((path for path in directory.iterdir() if path.is_dir() and (path/"strategy_spec.json").exists()),reverse=True) if directory.exists() else []
        if not versions:return DeploymentDecision(False,None,"strategy is not registered",None)
        active=directory/"active.json"
        selected=directory/json.loads(active.read_text(encoding="utf-8"))["version"] if active.exists() else versions[0]
        if selected not in versions:return DeploymentDecision(False,None,"active strategy version is missing",selected)
        payload=json.loads((selected/"strategy_spec.json").read_text(encoding="utf-8"));lifecycle=StrategyLifecycle(payload["lifecycle"])
        if simulated_venue:return DeploymentDecision(True,lifecycle,"simulation permits registered draft mechanics",selected)
        if lifecycle in (StrategyLifecycle.SUSPENDED,StrategyLifecycle.RETIRED):return DeploymentDecision(False,lifecycle,"strategy is suspended or retired",selected)
        if environment is Environment.LIVE:
            allowed=lifecycle in (StrategyLifecycle.LIVE_LIMITED,StrategyLifecycle.LIVE_APPROVED)
            return DeploymentDecision(allowed,lifecycle,"live lifecycle approved" if allowed else "live requires LIVE_LIMITED or LIVE_APPROVED",selected)
        allowed=lifecycle in (StrategyLifecycle.PAPER_APPROVED,StrategyLifecycle.LIVE_LIMITED,StrategyLifecycle.LIVE_APPROVED)
        return DeploymentDecision(allowed,lifecycle,"paper/testnet lifecycle approved" if allowed else "paper/testnet requires PAPER_APPROVED",selected)
