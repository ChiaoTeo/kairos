from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from kairospy import (
    BacktestCase,
    BacktestResult,
    BacktestSpec,
    DatasetRef,
    DatasetSetRef,
    Kairos,
    ResearchExperimentPolicy,
    ResearchPeriod,
    ResearchSpec,
)
from kairospy.system.apps.launch.application import BacktestApplication
from kairospy.client import ResearchClient
from kairospy.system.apps.workspace.application import WorkspaceApplication
import pytest


def _spec(case_id: str) -> BacktestSpec:
    return BacktestSpec(
        strategy="spy_put_spread.strategy:SpyPutSpread",
        data=DatasetSetRef(
            (
                DatasetRef(
                    dataset_id="market.quote/SPY/batch",
                    version="v1",
                    content_hash="a" * 64,
                    owner="market",
                    kind="quote",
                    subject="SPY-options",
                    start_time_unix_nanos=1,
                    end_time_unix_nanos=2,
                    event_count=2,
                ),
            )
        ),
        account=f"account-{case_id}",
        risk_profile="options-conservative",
        start="2025-01-01T00:00:00Z",
        end="2025-02-01T00:00:00Z",
        seed=42,
        launch_id=f"launch-{case_id}",
        strategy_params={"profit_exit": case_id},
    )


def _client(tmp_path: Path) -> tuple[Kairos, ResearchClient]:
    project = tmp_path / "research-batch"
    WorkspaceApplication().init_project(project, workspace_id="research-batch")
    kairos = Kairos.open(project)
    return kairos, kairos.research


def _locked_experiment(
    kairos: Kairos,
    values: tuple[str, ...],
) -> tuple[ResearchSpec, DatasetSetRef]:
    ref = kairos.data.catalog.publish(
        dataset_id="market.quote/SPY/research-concurrency",
        owner="market",
        kind="quote",
        subject="SPY",
        events=({"Quote": {"instrument_id": "SPY", "observed_at_unix_nanos": 1}},),
        lineage={"provider": "fixture"},
        quality_report={"event_count": 1},
    )
    dataset_set = DatasetSetRef(
        (ref,),
        composition_policy={
            "time_alignment": "point-in-time",
            "conflict_policy": "reject",
        },
    )
    kairos.data.publish_trust_report(dataset_set, required_kinds=("quote",))
    spec = ResearchSpec(
        research_id="spy-concurrency-study",
        hypothesis="Bounded Launch concurrency preserves experiment evidence",
        dataset_set=dataset_set,
        observation_rule="fixed fixture observation",
        feature_availability_rule="feature.available_at <= observation_time",
        label_rule="label.start_time > observation_time",
        in_sample=ResearchPeriod("2024-01-01T00:00:00Z", "2024-07-01T00:00:00Z"),
        validation=ResearchPeriod("2024-07-01T00:00:00Z", "2025-01-01T00:00:00Z"),
        holdout=ResearchPeriod("2025-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        baseline="single canonical Launch",
        seed=42,
        code_version="fixture-1",
        quote_stale_window_nanos=60_000_000_000,
        experiment=ResearchExperimentPolicy(
            parameter_space={"variant": values},
            maximum_trials=len(values),
            max_concurrency=2,
            selection_rule="input order",
        ),
    )
    kairos.research.pin_plan(spec)
    return spec, dataset_set


def test_research_facade_reuses_project_data_and_launch_adapter(
    tmp_path: Path,
) -> None:
    kairos, research = _client(tmp_path)

    assert kairos.research.data.workspace == kairos.data.workspace
    assert research.workspace == kairos.workspace


def test_project_client_exposes_canonical_launch_plan(tmp_path: Path) -> None:
    kairos, _ = _client(tmp_path)
    config = _spec("facade").to_launch_config(
        workspace_root=kairos.workspace.paths.root
    )

    assert kairos.launch_config(config) is config
    assert kairos.launch_plan(config) == config.plan()


def test_backtest_batch_bounds_concurrency_preserves_order_and_isolates_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kairos, client = _client(tmp_path)
    research, dataset_set = _locked_experiment(
        kairos, ("first", "failed", "third", "fourth")
    )
    active = 0
    maximum = 0

    async def fake_run(
        self: BacktestApplication, spec: BacktestSpec, *, timeout: float = 3600.0
    ) -> BacktestResult:
        nonlocal active, maximum
        del self, timeout
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        if spec.launch_id == "launch-failed":
            raise RuntimeError("fixture launch failed")
        return BacktestResult(
            launch_id=spec.launch_id,
            instance_id=f"instance-{spec.launch_id}",
            status="completed",
            normalized_config_hash=f"hash-{spec.launch_id}",
            report={"deterministic_result_sha256": f"report-{spec.launch_id}"},
        )

    monkeypatch.setattr(BacktestApplication, "run", fake_run)
    cases = tuple(
        BacktestCase(
            case_id=value,
            spec=replace(_spec(value), data=dataset_set),
            params={"variant": value},
        )
        for value in ("first", "failed", "third", "fourth")
    )

    batch = asyncio.run(client.run_backtests(research, cases, max_concurrency=2))

    assert maximum == 2
    assert batch.max_concurrency == 2
    assert batch.status == "failed"
    assert tuple(value.case_id for value in batch.cases) == (
        "first",
        "failed",
        "third",
        "fourth",
    )
    assert tuple(value.status for value in batch.cases) == (
        "completed",
        "failed",
        "completed",
        "completed",
    )
    assert batch.cases[1].error_type == "RuntimeError"
    assert batch.cases[1].error == "fixture launch failed"


def test_backtest_batch_requires_unique_case_and_launch_ids(tmp_path: Path) -> None:
    kairos, client = _client(tmp_path)
    research, dataset_set = _locked_experiment(kairos, ("one", "two"))
    spec = replace(_spec("same"), data=dataset_set)

    with pytest.raises(ValueError, match="case IDs"):
        asyncio.run(
            client.run_backtests(
                research,
                (
                    BacktestCase(
                        case_id="duplicate",
                        spec=spec,
                        params={"variant": "one"},
                    ),
                    BacktestCase(
                        case_id="duplicate",
                        spec=replace(_spec("other"), data=dataset_set),
                        params={"variant": "two"},
                    ),
                ),
            )
        )
    with pytest.raises(ValueError, match="Launch IDs"):
        asyncio.run(
            client.run_backtests(
                research,
                (
                    BacktestCase(case_id="one", spec=spec, params={"variant": "one"}),
                    BacktestCase(case_id="two", spec=spec, params={"variant": "two"}),
                ),
            )
        )


def test_research_backtest_experiment_enforces_locked_space_budget_and_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kairos, _ = _client(tmp_path)
    ref = kairos.data.catalog.publish(
        dataset_id="market.quote/SPY/research-batch",
        owner="market",
        kind="quote",
        subject="SPY",
        events=(
            {
                "Quote": {
                    "instrument_id": "SPY",
                    "observed_at_unix_nanos": 1,
                }
            },
        ),
        lineage={"provider": "fixture"},
        quality_report={"event_count": 1},
    )
    dataset_set = DatasetSetRef(
        (ref,),
        composition_policy={
            "time_alignment": "point-in-time",
            "conflict_policy": "reject",
        },
    )
    kairos.data.publish_trust_report(dataset_set, required_kinds=("quote",))
    research = ResearchSpec(
        research_id="spy-parameter-study",
        hypothesis="A fixed profit exit changes conservative performance",
        dataset_set=dataset_set,
        observation_rule="Wednesday 15:30 America/New_York",
        feature_availability_rule="feature.available_at <= observation_time",
        label_rule="label.start_time > observation_time",
        in_sample=ResearchPeriod("2024-01-01T00:00:00Z", "2024-07-01T00:00:00Z"),
        validation=ResearchPeriod("2024-07-01T00:00:00Z", "2025-01-01T00:00:00Z"),
        holdout=ResearchPeriod("2025-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        baseline="fixed 50 percent profit exit",
        seed=42,
        code_version="fixture-1",
        quote_stale_window_nanos=60_000_000_000,
        experiment=ResearchExperimentPolicy(
            parameter_space={"profit_exit": (0.4, 0.5)},
            maximum_trials=2,
            max_concurrency=2,
            selection_rule="lowest validation drawdown",
        ),
    )
    kairos.research.pin_plan(research)

    async def fake_run(
        self: BacktestApplication, spec: BacktestSpec, *, timeout: float = 3600.0
    ) -> BacktestResult:
        del self, timeout
        return BacktestResult(
            launch_id=spec.launch_id,
            instance_id=f"instance-{spec.launch_id}",
            status="completed",
            normalized_config_hash=f"hash-{spec.launch_id}",
            report={"deterministic_result_sha256": f"report-{spec.launch_id}"},
        )

    monkeypatch.setattr(BacktestApplication, "run", fake_run)
    base = replace(_spec("base"), data=dataset_set)
    cases = (
        BacktestCase(
            "exit-40",
            replace(base, launch_id="launch-exit-40"),
            {"profit_exit": 0.4},
        ),
        BacktestCase(
            "exit-50",
            replace(base, launch_id="launch-exit-50"),
            {"profit_exit": 0.5},
        ),
    )

    batch = asyncio.run(
        kairos.research.run_backtests(research, cases, max_concurrency=2)
    )

    assert batch.status == "completed"
    assert [case.params["profit_exit"] for case in batch.cases] == [0.4, 0.5]
    with pytest.raises(ValueError, match="outside plan"):
        asyncio.run(
            kairos.research.run_backtests(
                research,
                (
                    BacktestCase(
                        "exit-60",
                        replace(base, launch_id="launch-exit-60"),
                        {"profit_exit": 0.6},
                    ),
                ),
            )
        )
    with pytest.raises(ValueError, match="concurrency limit"):
        asyncio.run(kairos.research.run_backtests(research, cases, max_concurrency=3))
