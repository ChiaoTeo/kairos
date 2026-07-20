from __future__ import annotations

from dataclasses import asdict,dataclass
from datetime import date
import json
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TestWindowUse:
    __test__ = False
    study_id: str
    version: str
    start: str
    end: str
    purpose: str
    decision_oos: bool
    consumed: bool=True

    def __post_init__(self):
        if date.fromisoformat(self.start)>=date.fromisoformat(self.end):raise ValueError("test window must use [start, end)")


class TestWindowRegistry:
    __test__ = False
    def __init__(self,path: str|Path="data/studies/test_window_registry.jsonl") -> None:self.path=Path(path)
    def uses(self) -> tuple[TestWindowUse,...]:
        if not self.path.exists():return ()
        return tuple(TestWindowUse(**json.loads(line)) for line in self.path.read_text(encoding="utf-8").splitlines() if line)
    def register(self,use: TestWindowUse) -> None:
        if use in self.uses():return
        if use.decision_oos:
            conflicts=[old for old in self.uses() if old.consumed and old.study_id!=use.study_id and _overlap(old,use)]
            if conflicts:raise ValueError("decision-OOS window overlaps a previously consumed test window")
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.path.open("a",encoding="utf-8") as handle:handle.write(json.dumps(asdict(use),sort_keys=True)+"\n")


def _overlap(a,b):return max(a.start,b.start)<min(a.end,b.end)
