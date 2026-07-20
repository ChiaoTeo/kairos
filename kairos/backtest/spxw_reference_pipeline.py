from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from kairos.backtest.repository import BacktestRepository
from kairos.backtest.result import BacktestConfig
from kairos.backtest.experiment_runner import BacktestExperimentRunner
from kairos.data import DatasetStatus, QualityLevel, ResearchDataClient, RunMode
from kairos.risk.limits import RiskLimits
from kairos.strategies.bull_put_spread import BullPutSpreadConfig


def build_spxw_reference_pipeline(
    lake_root: str | Path,
    backtest_root: str | Path,
    *,
    event_release_id: str,
    source_slice_release_id: str,
    curated_slice_release_id: str,
) -> dict[str, object]:
    lake = Path(lake_root)
    client = ResearchDataClient(lake, run_mode=RunMode.BACKTEST)
    catalog = client.catalog
    event_release = catalog.release(event_release_id)
    source_release = catalog.release(source_slice_release_id)
    curated_release = catalog.release(curated_slice_release_id)
    if curated_release.quality_level not in {QualityLevel.BACKTEST, QualityLevel.PRODUCTION}:
        raise ValueError("SPXW reference pipeline requires a Q3/Q4 MarketSnapshot Release")
    if curated_release.status not in {DatasetStatus.APPROVED_FOR_BACKTEST, DatasetStatus.APPROVED_FOR_PRODUCTION}:
        raise ValueError("SPXW reference pipeline requires a backtest-approved MarketSnapshot Release")
    lineage_path = lake / curated_release.relative_path / "lineage.json"
    lineage = json.loads(lineage_path.read_text(encoding="utf-8"))
    input_ids = {str(item.get("release_id")) for item in lineage.get("inputs", [])}
    if {event_release.release_id, source_release.release_id} - input_ids:
        raise ValueError("curated SPXW Release lineage does not freeze Event and source MarketSnapshot inputs")
    feed = client.replay_snapshots(curated_release.release_id)
    dataset = feed.dataset
    repository = BacktestRepository(backtest_root)
    results = BacktestExperimentRunner(repository).run_suite(
        feed,
        BacktestConfig(dataset.manifest.start, dataset.manifest.end),
        BullPutSpreadConfig(),
        RiskLimits(),
    )
    runs = []
    for result in results:
        manifest = repository.load_manifest(str(result.run_id))
        runs.append({
            "run_id": str(result.run_id),
            "fill_model": result.config.fill_model,
            "status": result.status.value,
            "audit_hash": manifest["audit_hash"],
        })
    quality = json.loads((lake / curated_release.relative_path / "quality.json").read_text(encoding="utf-8"))
    payload = {
        "reference_schema_version": 1,
        "pipeline": "massive-spxw-event-market-snapshot-bull-put-v1",
        "inputs": {
            "event_release_id": event_release.release_id,
            "event_content_hash": event_release.content_hash,
            "source_slice_release_id": source_release.release_id,
            "source_slice_content_hash": source_release.content_hash,
            "curated_slice_release_id": curated_release.release_id,
            "curated_slice_content_hash": curated_release.content_hash,
            "quality_report_hash": quality.get("report_hash"),
        },
        "strategy": "bull-put-spread-v1",
        "consumed_inputs": [{
            "release_id": curated_release.release_id,
            "content_hash": curated_release.content_hash,
            "quality_level": curated_release.quality_level.value,
        }],
        "slice_count": dataset.manifest.slice_count,
        "runs": runs,
    }
    material = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["audit_hash"] = sha256(material.encode()).hexdigest()
    directory = Path(backtest_root) / "reference-scenarios" / payload["pipeline"]
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "manifest.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(target)
    payload["artifact"] = str(target)
    return payload
