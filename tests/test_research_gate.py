from __future__ import annotations

from dataclasses import replace

from kairospy import (
    DatasetRef,
    DatasetSetRef,
    ResearchExperimentPolicy,
    ResearchPeriod,
    ResearchSpec,
    Kairos,
)
from kairospy.application.research import ResearchGateApplication
from kairospy.application.workspace import WorkspaceApplication
import pytest


def _spec() -> ResearchSpec:
    dataset_set = DatasetSetRef(
        (
            DatasetRef(
                dataset_id="market.quote/SPY/research",
                version="v1",
                content_hash="a" * 64,
                owner="market",
                kind="quote",
                subject="SPY-options",
                start_time_unix_nanos=1,
                end_time_unix_nanos=2,
                event_count=2,
            ),
        ),
        composition_policy={
            "time_alignment": "point-in-time",
            "conflict_policy": "reject",
        },
    )
    return ResearchSpec(
        research_id="spy-put-iv-vs-rv",
        hypothesis="30-45 DTE 25 Delta Put IV exceeds subsequent realized volatility",
        dataset_set=dataset_set,
        observation_rule="Wednesday 15:30 America/New_York",
        feature_availability_rule="feature.available_at <= observation_time",
        label_rule="label.start_time > observation_time",
        in_sample=ResearchPeriod("2024-01-01T00:00:00Z", "2024-09-01T00:00:00Z"),
        validation=ResearchPeriod("2024-09-01T00:00:00Z", "2025-01-01T00:00:00Z"),
        holdout=ResearchPeriod("2025-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        baseline="unconditional future realized volatility",
        seed=42,
        code_version="commit-1",
        quote_stale_window_nanos=60_000_000_000,
    )


def _split() -> dict[str, object]:
    return {
        "sample_count": 100,
        "missing_rate": 0.01,
        "estimate": 0.03,
        "baseline_estimate": 0.0,
        "bootstrap_ci_lower": 0.01,
        "bootstrap_ci_upper": 0.05,
        "cost_scenarios": {
            "mid_sensitivity": 0.04,
            "conservative_bid_ask": 0.03,
            "fees": 0.025,
            "stress_slippage": 0.015,
        },
    }


def test_research_gate_binds_fixed_plan_data_holdout_costs_and_conclusion() -> None:
    spec = _spec()
    kwargs = {
        "data_trust_report": {
            "status": "passed",
            "composition_hash": spec.dataset_set.composition_hash,
        },
        "results": {
            "in_sample": _split(),
            "validation": _split(),
            "holdout": _split(),
        },
        "conclusion": "accepted",
        "limitations": ("historical Quote coverage is sparse",),
    }

    first = ResearchGateApplication().evaluate(spec, **kwargs)
    second = ResearchGateApplication().evaluate(spec, **kwargs)

    assert first == second
    assert first["status"] == "passed"
    assert first["research_plan_hash"] == spec.plan_hash
    assert first["results"]["holdout"]["cost_scenarios"]["stress_slippage"] == 0.015


def test_research_gate_rejects_future_features_or_incomplete_costs() -> None:
    with pytest.raises(ValueError, match="Point-in-time"):
        replace(_spec(), feature_availability_rule="feature time is unspecified")
    spec = _spec()
    invalid = _split()
    invalid["cost_scenarios"] = {"mid_sensitivity": 0.04}
    with pytest.raises(ValueError, match="every fixed cost scenario"):
        ResearchGateApplication().evaluate(
            spec,
            data_trust_report={
                "status": "passed",
                "composition_hash": spec.dataset_set.composition_hash,
            },
            results={
                "in_sample": _split(),
                "validation": _split(),
                "holdout": invalid,
            },
            conclusion="insufficient-evidence",
            limitations=("limited coverage",),
        )


def test_research_plan_locks_parameter_search_budget_and_holdout_rule() -> None:
    base = _spec()
    experiment = ResearchExperimentPolicy(
        parameter_space={
            "profit_exit": (0.4, 0.5, 0.6),
            "time_exit_dte": (14, 21),
        },
        maximum_trials=6,
        max_concurrency=2,
        selection_rule="highest validation return subject to drawdown <= 10%",
    )
    spec = replace(base, experiment=experiment)

    rebuilt = ResearchSpec.from_dict(spec.as_dict())

    assert rebuilt == spec
    assert rebuilt.plan_hash == spec.plan_hash
    assert rebuilt.plan_hash != base.plan_hash
    assert rebuilt.experiment.maximum_trials == 6
    assert rebuilt.experiment.max_concurrency == 2
    with pytest.raises(ValueError, match="Holdout"):
        ResearchExperimentPolicy(holdout_parameter_changes=True)
    with pytest.raises(ValueError, match="concurrency"):
        ResearchExperimentPolicy(maximum_trials=1, max_concurrency=2)


def test_project_research_client_requires_and_persists_matching_data_gate(
    tmp_path,
) -> None:
    project = tmp_path / "research-project"
    WorkspaceApplication().init_project(project, workspace_id="research-project")
    kairos = Kairos.open(project)
    dataset = kairos.data.catalog.publish(
        dataset_id="market.quote/SPY/research",
        owner="market",
        kind="quote",
        subject="SPY-options",
        events=(
            {
                "Quote": {
                    "instrument_id": "SPY-P-100",
                    "observed_at_unix_nanos": 1,
                }
            },
        ),
        lineage={"provider": "fixture"},
        quality_report={"event_count": 1},
    )
    dataset_set = DatasetSetRef(
        (dataset,),
        composition_policy={
            "time_alignment": "point-in-time",
            "conflict_policy": "reject",
        },
    )
    kairos.data.publish_trust_report(dataset_set, required_kinds=("quote",))
    template = _spec()
    spec = replace(template, dataset_set=dataset_set)
    results = {
        "in_sample": _split(),
        "validation": _split(),
        "holdout": _split(),
    }

    plan_lock = kairos.research.pin_plan(spec)
    report = kairos.research.publish_gate(
        spec,
        results=results,
        conclusion="insufficient-evidence",
        limitations=("fixture evidence",),
    )

    assert report["status"] == "passed"
    assert plan_lock["status"] == "locked"
    assert report["research_plan_lock"]["locked_at"] == plan_lock["locked_at"]
    assert kairos.research.gate_report(spec.plan_hash) == report


def test_project_research_gate_refuses_an_unlocked_plan(tmp_path) -> None:
    project = tmp_path / "unlocked-research"
    WorkspaceApplication().init_project(project, workspace_id="unlocked-research")
    kairos = Kairos.open(project)
    dataset = kairos.data.catalog.publish(
        dataset_id="market.quote/SPY/unlocked",
        owner="market",
        kind="quote",
        subject="SPY-options",
        events=({"Quote": {"instrument_id": "SPY", "observed_at_unix_nanos": 1}},),
        lineage={"provider": "fixture"},
        quality_report={"event_count": 1},
    )
    dataset_set = DatasetSetRef(
        (dataset,),
        composition_policy={
            "time_alignment": "point-in-time",
            "conflict_policy": "reject",
        },
    )
    kairos.data.publish_trust_report(dataset_set, required_kinds=("quote",))
    spec = replace(_spec(), dataset_set=dataset_set)

    with pytest.raises(FileNotFoundError, match="locked Research plan"):
        kairos.research.publish_gate(
            spec,
            results={
                "in_sample": _split(),
                "validation": _split(),
                "holdout": _split(),
            },
            conclusion="insufficient-evidence",
            limitations=("unlocked",),
        )
