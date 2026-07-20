"""Scenario 1-2: flexible study workspace -> frozen candidate -> governed Factor Release."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from examples.backtest.governed_sma import canonical_events, fixture_bars
from kairospy.features import FactorRegistry, SmaFactorConfig, SmaFactorRuntime, batch_sma_factors, snapshots_hash
from kairospy.study_platform import StudyWorkspace, StudyWorkspaceRepository


def run(root: Path) -> dict[str, object]:
    bars = fixture_bars(); input_hash = "f"*64
    studies = StudyWorkspaceRepository(root)
    workspace_path = studies.create(StudyWorkspace(
        "btc-sma-exploration", "1.0.0", "SMA spread may explain next-period direction",
        "fixture:sma-bars-v1", input_hash, "available_time",
        bars[0].start.isoformat(), bars[-1].end.isoformat(),
        created_at=datetime(2026, 7, 17, tzinfo=timezone.utc).isoformat(),
    ))
    candidate = studies.freeze("btc-sma-exploration", "1.0.0")
    config = SmaFactorConfig(5, 15)
    runtime = SmaFactorRuntime(config, input_identity="fixture:sma-bars-v1")
    factor_directory = FactorRegistry(root/"factors").register(runtime.spec)
    batch = batch_sma_factors(bars, config, input_identity="fixture:sma-bars-v1")
    replay = tuple(
        snapshot for event in canonical_events(bars)
        if (snapshot := runtime.update(event)) is not None
    )
    return {
        "sandbox_workspace": workspace_path.exists(),
        "frozen_candidate": (candidate/"manifest.json").exists(),
        "factor_release": (factor_directory/"manifest.json").exists(),
        "batch_replay_equal": batch == replay,
        "factor_hash": snapshots_hash(batch),
        "observations": len(batch),
        "ready_observations": sum(item.quality.value == "ready" for item in batch),
    }


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        print(json.dumps(run(Path(directory)), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
