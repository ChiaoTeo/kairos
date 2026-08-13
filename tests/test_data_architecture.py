from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from kairospy.application.data import (
    DataRequirement,
    DatasetCatalogApplication,
    DatasetReaderApplication,
    DatasetSetRef,
    OptionMarketDataTarget,
)
from kairospy.application.workspace import WorkspaceApplication
from kairospy import DataUnavailableError, Kairos


def _quote(time: int, instrument: str = "SPY") -> dict:
    return {
        "Quote": {
            "market_id": "market:opra",
            "instrument_id": instrument,
            "bid_price": "100",
            "ask_price": "101",
            "observed_at_unix_nanos": time,
            "available_at_unix_nanos": time + 1,
            "source_id": "fixture",
        }
    }


def _catalog(tmp_path: Path) -> DatasetCatalogApplication:
    workspace = WorkspaceApplication().init_project(
        tmp_path / "project", workspace_id="data-contract"
    )
    return DatasetCatalogApplication(workspace)


def _project(tmp_path: Path, name: str) -> Path:
    project = tmp_path / name
    WorkspaceApplication().init_project(project, workspace_id=name)
    return project


def test_data_public_surface_does_not_eagerly_load_strategy_runtime() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from kairospy import Kairos; "
                "assert 'kairospy.application.strategy' not in sys.modules; "
                "assert Kairos.__name__ == 'Kairos'"
            ),
        ],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_atomic_dataset_publication_is_immutable_and_storage_independent(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)

    first = catalog.publish(
        dataset_id="market.quote/SPY/options",
        owner="market",
        kind="quote",
        subject="SPY-options",
        product="options",
        source="fixture",
        events=(_quote(2), _quote(1)),
    )
    repeated = catalog.publish(
        dataset_id="market.quote/SPY/options",
        owner="market",
        kind="quote",
        subject="SPY-options",
        product="options",
        source="fixture",
        events=(_quote(2), _quote(1)),
    )
    changed = catalog.publish(
        dataset_id="market.quote/SPY/options",
        owner="market",
        kind="quote",
        subject="SPY-options",
        product="options",
        source="fixture",
        events=(_quote(3),),
    )

    assert repeated == first
    assert changed.version != first.version
    assert changed.content_hash != first.content_hash
    assert "path" not in first.as_dict()
    assert catalog.data_path(first).is_relative_to(
        catalog.workspace.paths.root / "data" / "market" / "datasets"
    )


def test_partitioned_dataset_is_one_atomic_ref_and_one_logical_snapshot(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)
    published = catalog.publish_partitions(
        dataset_id="market.quote/SPY/partitioned",
        owner="market",
        kind="quote",
        subject="SPY",
        source="fixture",
        partitions=(
            ("event-date=2025-01-02", (_quote(20), _quote(10))),
            ("event-date=2025-01-03", (_quote(40), _quote(30))),
        ),
    )

    assert published.event_count == 4
    assert len(catalog.data_paths(published)) == 2
    with pytest.raises(ValueError, match="multiple internal partitions"):
        catalog.data_path(published)
    readers = DatasetReaderApplication(catalog)
    snapshot = readers.snapshot(readers.plan(DatasetSetRef((published,))))
    assert [event["Quote"]["observed_at_unix_nanos"] for event in snapshot.scan()] == [
        10,
        20,
        30,
        40,
    ]


def test_dataset_description_exposes_quality_without_physical_paths(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)
    ref = catalog.publish_partitions(
        dataset_id="market.quote/SPY/options",
        owner="market",
        kind="quote",
        subject="SPY-options",
        partitions=(
            ("2025-01-02", (_quote(10),)),
            ("2025-01-03", (_quote(20),)),
        ),
        lineage={
            "provider": "massive",
            "operation": "historical-quotes",
            "provider_manifest": {
                "file": "/private/acquisition.jsonl",
                "credential_id": "massive-readonly",
            },
        },
        quality_report={"status": "passed", "crossed_quotes": 0},
    )

    description = catalog.describe(ref.dataset_id, ref.version)

    assert description.ref == ref
    assert description.lineage["provider"] == "massive"
    assert "file" not in description.lineage["provider_manifest"]
    assert description.quality_report["status"] == "passed"
    assert [item.key for item in description.partitions] == [
        "2025-01-02",
        "2025-01-03",
    ]
    assert sum(item.event_count for item in description.partitions) == 2
    assert "path" not in repr(description)
    assert all("/" not in item.format for item in description.partitions)


def test_invalid_partition_prevents_the_entire_dataset_publication(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)

    with pytest.raises(ValueError, match="kind mismatch"):
        catalog.publish_partitions(
            dataset_id="market.quote/SPY/rejected",
            owner="market",
            kind="quote",
            subject="SPY",
            partitions=(("valid", (_quote(10),)), ("invalid", ({"Trade": {}},))),
        )

    assert catalog.list() == ()
    assert not catalog.staging_root.exists()


def test_publish_recovers_commit_after_data_rename_before_catalog_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _catalog(tmp_path)
    original = DatasetCatalogApplication._write_entries
    failures = 0

    def interrupt(_self, _entries):
        nonlocal failures
        failures += 1
        raise OSError("simulated catalog interruption")

    monkeypatch.setattr(DatasetCatalogApplication, "_write_entries", interrupt)
    with pytest.raises(OSError, match="catalog interruption"):
        catalog.publish(
            dataset_id="market.quote/SPY/recoverable",
            owner="market",
            kind="quote",
            subject="SPY",
            events=(_quote(10),),
        )
    assert catalog.list() == ()

    monkeypatch.setattr(DatasetCatalogApplication, "_write_entries", original)
    recovered = catalog.publish(
        dataset_id="market.quote/SPY/recoverable",
        owner="market",
        kind="quote",
        subject="SPY",
        events=(_quote(10),),
    )

    assert failures == 1
    assert catalog.list() == (recovered,)
    assert catalog.data_path(recovered).is_file()


def test_concurrent_identical_publications_commit_one_catalog_entry(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)

    def publish(_index: int):
        return catalog.publish(
            dataset_id="market.quote/SPY/concurrent",
            owner="market",
            kind="quote",
            subject="SPY",
            events=(_quote(20), _quote(10)),
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = tuple(executor.map(publish, range(8)))

    assert len({result.identity for result in results}) == 1
    assert catalog.list() == (results[0],)
    assert not tuple(catalog.staging_root.iterdir())


def test_resolve_is_read_only_and_plan_explains_missing_coverage(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)
    catalog.publish(
        dataset_id="market.quote/SPY/options",
        owner="market",
        kind="quote",
        subject="SPY-options",
        events=(_quote(10), _quote(20)),
        source="fixture",
    )
    covered = DataRequirement(
        owner="market",
        kind="quote",
        subject="SPY-options",
        start_time_unix_nanos=10,
        end_time_unix_nanos=20,
        source="fixture",
    )
    missing = DataRequirement(
        owner="reference",
        kind="option-contract",
        subject="SPY",
        start_time_unix_nanos=10,
        end_time_unix_nanos=20,
        source="fixture-reference",
    )

    dataset_set, gaps = catalog.resolve((covered, missing))
    plan = catalog.plan((covered, missing))

    assert dataset_set is None
    assert len(gaps) == 1
    assert gaps[0].requirement == missing
    assert len(plan.satisfied) == 1
    assert plan.missing == gaps
    assert plan.steps[0].provider == "fixture-reference"
    assert plan.plan_hash
    reviewed = plan.as_dict()
    assert reviewed["steps"][0]["target_dataset_id"]
    assert reviewed["steps"][0]["credential_id"] is None
    assert reviewed["steps"][0]["phases"] == (
        "acquire",
        "normalize",
        "validate",
        "prepare",
        "publish",
    )
    assert reviewed["steps"][0]["blocked_reason"] is None


def test_data_requirement_accepts_credential_reference_but_rejects_secret() -> None:
    requirement = DataRequirement(
        owner="market",
        kind="quote",
        subject="SPY",
        parameters={"credential_id": "massive-readonly"},
    )
    assert requirement.parameters["credential_id"] == "massive-readonly"
    with pytest.raises(ValueError, match="cannot contain secrets"):
        DataRequirement(
            owner="market",
            kind="quote",
            subject="SPY",
            parameters={"api_key": "must-not-enter-a-plan"},
        )


def test_snapshot_and_replay_share_one_read_plan_and_fact_set(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    underlying = catalog.publish(
        dataset_id="market.quote/SPY",
        owner="market",
        kind="quote",
        subject="SPY",
        events=(_quote(30), _quote(10)),
        source="fixture",
    )
    options = catalog.publish(
        dataset_id="market.quote/SPY/options",
        owner="market",
        kind="quote",
        subject="SPY-options",
        events=(_quote(20, "SPY-P-100"), _quote(40, "SPY-P-100")),
        source="fixture",
    )
    dataset_set = DatasetSetRef((underlying, options))
    readers = DatasetReaderApplication(catalog)
    read_plan = readers.plan(
        dataset_set,
        start_time_unix_nanos=10,
        end_time_unix_nanos=30,
    )

    snapshot = readers.snapshot(read_plan)
    replay = readers.replay(read_plan)

    assert snapshot.plan is read_plan
    assert replay.plan is read_plan
    assert list(replay) == list(snapshot.scan())
    assert [event["Quote"]["observed_at_unix_nanos"] for event in snapshot.scan()] == [
        10,
        20,
        30,
    ]
    assert replay.eof is True


def test_lazy_analytical_view_uses_the_same_bounded_read_plan(tmp_path: Path) -> None:
    pytest.importorskip("polars")
    catalog = _catalog(tmp_path)
    quotes = catalog.publish_partitions(
        dataset_id="market.quote/SPY/lazy",
        owner="market",
        kind="quote",
        subject="SPY",
        partitions=(
            ("event-date=one", (_quote(10), _quote(20))),
            ("event-date=two", (_quote(30), _quote(40))),
        ),
    )
    readers = DatasetReaderApplication(catalog)
    read_plan = readers.plan(
        DatasetSetRef((quotes,)),
        start_time_unix_nanos=15,
        end_time_unix_nanos=35,
    )

    rows = (
        readers.analytical(read_plan)
        .scan("quote", columns=("instrument_id", "observed_at_unix_nanos"))
        .collect()
        .to_dicts()
    )

    assert rows == [
        {"instrument_id": "SPY", "observed_at_unix_nanos": 20},
        {"instrument_id": "SPY", "observed_at_unix_nanos": 30},
    ]


def test_greeks_envelope_maps_to_option_greeks_atomic_kind(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    ref = catalog.publish(
        dataset_id="market.option-greeks/SPY/test",
        owner="market",
        kind="option-greeks",
        subject="SPY-options",
        events=(
            {
                "Greeks": {
                    "market_id": "market:opra",
                    "instrument_id": "SPY-P-100",
                    "observed_at_unix_nanos": 10,
                    "source_id": "derived",
                    "derivation": "test-model",
                }
            },
        ),
    )
    readers = DatasetReaderApplication(catalog)
    plan = readers.plan(DatasetSetRef((ref,)), kinds=("greeks",))

    assert len(readers.snapshot(plan).scan("option-greeks")) == 1


def test_derived_greeks_publication_binds_parent_set_and_reference_lineage(
    tmp_path: Path,
) -> None:
    kairos = Kairos.open(_project(tmp_path, "derived-greeks"))
    quote = kairos.data.catalog.publish(
        dataset_id="market.quote/SPY/parents",
        owner="market",
        kind="quote",
        subject="SPY-options",
        events=(_quote(10, "SPY-P-100"),),
    )
    parents = DatasetSetRef(
        (quote,),
        composition_policy={
            "time_alignment": "point-in-time",
            "conflict_policy": "reject",
        },
    )

    derived = asyncio.run(
        kairos.data.publish_derived(
            dataset_id="market.option-greeks/SPY/model-v1",
            owner="market",
            kind="option-greeks",
            subject="SPY-options",
            events=(
                {
                    "Greeks": {
                        "instrument_id": "SPY-P-100",
                        "observed_at_unix_nanos": 10,
                        "available_at_unix_nanos": 11,
                        "derivation": "black-scholes-european-v1",
                    }
                },
            ),
            parents=parents,
            derivation="black-scholes-european-v1",
            availability_semantics="derived",
            reference_snapshot_id="reference-SPY-10",
            quality_report={"event_count": 1, "invalid_events": 0},
        )
    )
    description = kairos.data.describe(derived.dataset_id, version=derived.version)

    assert description.lineage["parent_composition_hash"] == parents.composition_hash
    assert description.lineage["parents"][0]["dataset_id"] == quote.dataset_id
    assert description.lineage["availability_semantics"] == "derived"
    assert derived.reference_snapshot_id == "reference-SPY-10"

    with pytest.raises(ValueError, match="Reference snapshot"):
        kairos.data.catalog.publish_derived(
            dataset_id="market.option-greeks/SPY/invalid",
            owner="market",
            kind="option-greeks",
            subject="SPY-options",
            events=(),
            parents=parents,
            derivation="model",
            availability_semantics="derived",
        )


def test_lazy_point_in_time_join_never_uses_future_available_fact(
    tmp_path: Path,
) -> None:
    pytest.importorskip("polars")
    catalog = _catalog(tmp_path)
    quotes = catalog.publish(
        dataset_id="market.quote/SPY/pit",
        owner="market",
        kind="quote",
        subject="SPY-options",
        events=(_quote(20, "SPY-P-100"), _quote(30, "SPY-P-100")),
    )
    contracts = catalog.publish(
        dataset_id="reference.option-contract/SPY/pit",
        owner="reference",
        kind="option-contract",
        subject="SPY",
        events=(
            {
                "kind": "option-contract",
                "instrument_id": "SPY-P-100",
                "observed_at_unix_nanos": 10,
                "available_at_unix_nanos": 10,
                "reference_snapshot_id": "snapshot-10",
            },
            {
                "kind": "option-contract",
                "instrument_id": "SPY-P-100",
                "observed_at_unix_nanos": 25,
                "available_at_unix_nanos": 25,
                "reference_snapshot_id": "snapshot-25",
            },
        ),
    )
    readers = DatasetReaderApplication(catalog)
    plan = readers.plan(DatasetSetRef((quotes, contracts)))

    rows = (
        readers.analytical(plan)
        .point_in_time_join("quote", "option-contract")
        .select(
            "observed_at_unix_nanos",
            "available_at_unix_nanos_right",
            "reference_snapshot_id",
        )
        .collect()
        .to_dicts()
    )

    assert rows == [
        {
            "observed_at_unix_nanos": 20,
            "available_at_unix_nanos_right": 10,
            "reference_snapshot_id": "snapshot-10",
        },
        {
            "observed_at_unix_nanos": 30,
            "available_at_unix_nanos_right": 25,
            "reference_snapshot_id": "snapshot-25",
        },
    ]
    assert all(
        row["available_at_unix_nanos_right"] <= row["observed_at_unix_nanos"]
        for row in rows
    )


def test_analytical_scan_honors_read_plan_kind_filter(tmp_path: Path) -> None:
    pytest.importorskip("polars")
    catalog = _catalog(tmp_path)
    quotes = catalog.publish(
        dataset_id="market.quote/SPY/kind-filter",
        owner="market",
        kind="quote",
        subject="SPY",
        events=(_quote(10),),
    )
    readers = DatasetReaderApplication(catalog)
    plan = readers.plan(DatasetSetRef((quotes,)), kinds=("bar",))

    with pytest.raises(LookupError, match="outside the shared read plan"):
        readers.analytical(plan).scan("quote")


def test_data_trust_gate_produces_direct_manifest_and_pit_evidence(
    tmp_path: Path,
) -> None:
    kairos = Kairos.open(_project(tmp_path, "gate-project"))
    quote = kairos.data.catalog.publish(
        dataset_id="market.quote/SPY/gate",
        owner="market",
        kind="quote",
        subject="SPY-options",
        events=(_quote(10, "SPY-P-100"),),
        lineage={"provider": "massive"},
        quality_report={"event_count": 1, "crossed_quotes": 0},
    )
    contract = kairos.data.catalog.publish(
        dataset_id="reference.option-contract/SPY/gate",
        owner="reference",
        kind="option-contract",
        subject="SPY",
        events=(
            {
                "kind": "option-contract",
                "instrument_id": "SPY-P-100",
                "observed_at_unix_nanos": 10,
                "available_at_unix_nanos": 10,
            },
        ),
        reference_snapshot_id="reference-SPY-10",
        lineage={"provider": "massive", "as_of": "2025-01-02"},
        quality_report={"reference_match_rate": 1.0, "invalid_contracts": 0},
    )
    dataset_set = DatasetSetRef(
        (quote, contract),
        composition_policy={
            "time_alignment": "point-in-time",
            "conflict_policy": "reject",
            "replay_tie_breaker": "owner-kind-source-instrument",
        },
    )

    report = kairos.data.publish_trust_report(
        dataset_set, required_kinds=("quote", "option-contract")
    )

    assert report.status == "passed"
    assert report.failed_checks == ()
    assert report.composition_hash == dataset_set.composition_hash
    assert report.as_dict()["gate"] == "data-trust"
    persisted = kairos.data.trust_report(dataset_set.composition_hash)
    assert persisted["status"] == "passed"
    assert persisted["composition_hash"] == dataset_set.composition_hash


def test_data_trust_gate_rejects_option_market_without_pit_reference_match(
    tmp_path: Path,
) -> None:
    kairos = Kairos.open(_project(tmp_path, "gate-reference-mismatch"))
    quote = kairos.data.catalog.publish(
        dataset_id="market.quote/SPY/reference-mismatch",
        owner="market",
        kind="quote",
        subject="SPY-options",
        product="options",
        events=(_quote(20, "SPY-P-MISSING"),),
        lineage={"provider": "massive"},
        quality_report={"event_count": 1},
    )
    future_contract = kairos.data.catalog.publish(
        dataset_id="reference.option-contract/SPY/reference-mismatch",
        owner="reference",
        kind="option-contract",
        subject="SPY",
        events=(
            {
                "kind": "option-contract",
                "instrument_id": "SPY-P-MISSING",
                "observed_at_unix_nanos": 30,
                "available_at_unix_nanos": 30,
            },
        ),
        reference_snapshot_id="reference-SPY-30",
        lineage={"provider": "massive", "as_of": "2025-01-03"},
        quality_report={"reference_match_rate": 1.0},
    )
    dataset_set = DatasetSetRef(
        (quote, future_contract),
        composition_policy={
            "time_alignment": "point-in-time",
            "conflict_policy": "reject",
        },
    )

    report = kairos.data.validate_trust(
        dataset_set, required_kinds=("quote", "option-contract")
    )

    assert report.status == "failed"
    check = next(
        value
        for value in report.checks
        if value.name == "option_market_point_in_time_reference_match"
    )
    assert check.status == "failed"
    assert check.detail["reference_match_rate"] == 0.0
    assert check.detail["future_only_instruments"] == ["SPY-P-MISSING"]


def test_named_dataset_set_alias_moves_without_mutating_old_composition(
    tmp_path: Path,
) -> None:
    kairos = Kairos.open(_project(tmp_path, "named-sets"))
    first = kairos.data.catalog.publish(
        dataset_id="market.quote/SPY/named",
        owner="market",
        kind="quote",
        subject="SPY",
        events=(_quote(10),),
    )
    second = kairos.data.catalog.publish(
        dataset_id="market.quote/SPY/named",
        owner="market",
        kind="quote",
        subject="SPY",
        events=(_quote(20),),
    )
    policy = {"time_alignment": "point-in-time", "conflict_policy": "reject"}
    original = DatasetSetRef((first,), composition_policy=policy)
    replacement = DatasetSetRef((second,), composition_policy=policy)

    kairos.data.pin_set("spy-options-research", original)
    kairos.data.pin_set(
        "spy-options-research",
        replacement,
        expected_current_hash=original.composition_hash,
    )

    assert kairos.data.load_set("spy-options-research") == replacement
    assert (
        kairos.data.load_set(
            "spy-options-research", composition_hash=original.composition_hash
        )
        == original
    )
    assert (
        kairos.data.set_aliases()["spy-options-research"]
        == replacement.composition_hash
    )
    with pytest.raises(ValueError, match="alias changed"):
        kairos.data.pin_set(
            "spy-options-research",
            original,
            expected_current_hash=original.composition_hash,
        )


def test_option_market_preparation_builds_deterministic_atomic_requirements(
    tmp_path: Path,
) -> None:
    kairos = Kairos.open(_project(tmp_path, "option-preparation"))
    later = OptionMarketDataTarget.from_reference_event(
        {
            "kind": "option-contract",
            "underlying": "SPY",
            "provider_symbol": "O:SPY250221P00580000",
            "instrument_id": "instrument:option:SPY:20250221:580:P",
            "market_id": "market:massive:options:O:SPY250221P00580000",
        },
        start_time_unix_nanos=20,
        end_time_unix_nanos=30,
    )
    earlier = OptionMarketDataTarget.from_reference_event(
        {
            "kind": "option-contract",
            "underlying": "SPY",
            "provider_symbol": "O:SPY250117P00570000",
            "instrument_id": "instrument:option:SPY:20250117:570:P",
            "market_id": "market:massive:options:O:SPY250117P00570000",
        },
        start_time_unix_nanos=10,
        end_time_unix_nanos=15,
    )

    requirements = kairos.data.option_market_requirements(
        (later, earlier, later), kinds=("quote", "trade")
    )

    assert len(requirements) == 4
    assert [item.parameters["symbol"] for item in requirements] == [
        "O:SPY250117P00570000",
        "O:SPY250117P00570000",
        "O:SPY250221P00580000",
        "O:SPY250221P00580000",
    ]
    assert [item.kind for item in requirements] == [
        "quote",
        "trade",
        "quote",
        "trade",
    ]
    assert all(item.subject.startswith("SPY-options/O:SPY") for item in requirements)
    assert all(
        item.parameters["credential_id"] == "massive-readonly" for item in requirements
    )
    assert len({item.subject for item in requirements}) == 2


def test_replay_checkpoint_is_bound_to_read_plan(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    dataset = catalog.publish(
        dataset_id="market.quote/SPY",
        owner="market",
        kind="quote",
        subject="SPY",
        events=(_quote(10), _quote(20)),
    )
    readers = DatasetReaderApplication(catalog)
    plan = readers.plan(DatasetSetRef((dataset,)))
    replay = readers.replay(plan)
    assert next(replay) == _quote(10)
    checkpoint = replay.checkpoint()

    resumed = replay.resume(checkpoint)

    assert list(resumed) == [_quote(20)]
    with pytest.raises(ValueError, match="another read plan"):
        replay.resume(checkpoint | {"read_plan_hash": "different"})


def test_replay_materialization_records_the_shared_read_plan(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    dataset = catalog.publish(
        dataset_id="market.quote/SPY",
        owner="market",
        kind="quote",
        subject="SPY",
        events=(_quote(10), _quote(20)),
    )
    readers = DatasetReaderApplication(catalog)
    plan = readers.plan(DatasetSetRef((dataset,)), end_time_unix_nanos=10)

    target = readers.materialize_replay(plan, tmp_path / "instance" / "replay.jsonl")

    assert target.read_text(encoding="utf-8").count("\n") == 1
    identity = target.with_suffix(".jsonl.read-plan.json").read_text(encoding="utf-8")
    assert plan.plan_hash in identity
    assert plan.dataset_set.composition_hash in identity


def test_atomic_dataset_rejects_mixed_fact_kinds(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)

    with pytest.raises(ValueError, match="kind mismatch"):
        catalog.publish(
            dataset_id="market.quote/SPY",
            owner="market",
            kind="quote",
            subject="SPY",
            events=(_quote(1), {"Trade": {"observed_at_unix_nanos": 2}}),
        )


def test_kairos_data_client_binds_all_operations_to_explicit_project(
    tmp_path: Path,
) -> None:
    first_workspace = WorkspaceApplication().init_project(
        tmp_path / "workspace1", workspace_id="workspace1"
    )
    WorkspaceApplication().init_project(
        tmp_path / "workspace2", workspace_id="workspace2"
    )
    first = Kairos.open(tmp_path / "workspace1")
    second = Kairos.open(tmp_path / "workspace2" / ".kairos")
    requirement = DataRequirement(owner="market", kind="quote", subject="SPY")

    published = asyncio.run(
        first.data.publish(
            dataset_id="market.quote/SPY",
            owner="market",
            kind="quote",
            subject="SPY",
            events=(_quote(1),),
        )
    )
    resolved = asyncio.run(first.data.resolve((requirement,)))

    assert first.workspace == first_workspace
    assert resolved.members == (published,)
    assert second.workspace.workspace_id == "workspace2"
    with pytest.raises(DataUnavailableError):
        asyncio.run(second.data.resolve((requirement,)))


def test_reviewed_massive_plan_executes_through_market_application_and_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    WorkspaceApplication().init_project(tmp_path / "project", workspace_id="acquire")
    kairos = Kairos.open(tmp_path / "project")
    requirement = DataRequirement(
        owner="market",
        kind="quote",
        subject="SPY-options",
        product="options",
        source="massive",
        venue="OPRA",
        cadence="tick",
        start_time_unix_nanos=10_000_000,
        end_time_unix_nanos=20_000_000,
        parameters={
            "symbol": "O:SPY250117P00500000",
            "instrument_id": "instrument:option:SPY:20250117:500:P",
            "credential_id": "massive-readonly",
        },
    )
    invoked: list[list[str]] = []

    def run(_self, arguments):
        values = list(arguments)
        invoked.append(values)
        target = Path(values[values.index("--file") + 1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "Quote": {
                        "market_id": "market:massive:options:O:SPY250117P00500000",
                        "instrument_id": "instrument:option:SPY:20250117:500:P",
                        "bid_price": "1.00",
                        "bid_quantity": "2",
                        "ask_price": "1.10",
                        "ask_quantity": "3",
                        "observed_at_unix_nanos": 15_000_000,
                        "source_id": "massive",
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return {"event_count": 1, "provider": "massive"}

    monkeypatch.setattr("kairospy.application.market.cli.MarketCliApplication.run", run)

    plan = asyncio.run(kairos.data.plan((requirement,)))
    dataset_set = asyncio.run(kairos.data.execute(plan))

    assert plan.missing[0].requirement == requirement
    assert invoked and invoked[0][:3] == ["download", "--provider", "massive"]
    assert "--api-key" not in invoked[0]
    assert dataset_set.members[0].start_time_unix_nanos == 10_000_000
    assert dataset_set.members[0].end_time_unix_nanos == 20_000_000
    assert asyncio.run(kairos.data.resolve((requirement,))) == dataset_set


def test_market_acquisition_does_not_publish_empty_validated_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    WorkspaceApplication().init_project(
        tmp_path / "project", workspace_id="empty-acquisition"
    )
    kairos = Kairos.open(tmp_path / "project")
    requirement = DataRequirement(
        owner="market",
        kind="quote",
        subject="SPY-options/O:SPY-NO-DATA",
        product="options",
        source="massive",
        start_time_unix_nanos=10_000_000,
        end_time_unix_nanos=20_000_000,
        parameters={
            "symbol": "O:SPY-NO-DATA",
            "instrument_id": "instrument:SPY-NO-DATA",
            "credential_id": "massive-readonly",
        },
    )

    def run(_self, arguments):
        values = list(arguments)
        target = Path(values[values.index("--file") + 1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
        return {"event_count": 0, "provider": "massive"}

    monkeypatch.setattr("kairospy.application.market.cli.MarketCliApplication.run", run)
    plan = asyncio.run(kairos.data.plan((requirement,)))

    with pytest.raises(ValueError, match="returned no facts"):
        asyncio.run(kairos.data.execute(plan))

    assert kairos.data.list() == ()
    assert kairos.data.execution(plan.plan_hash)["steps"][0]["status"] == "failed"


def test_failed_acquisition_resumes_by_reusing_completed_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    WorkspaceApplication().init_project(tmp_path / "project", workspace_id="resume")
    kairos = Kairos.open(tmp_path / "project")
    requirements = tuple(
        DataRequirement(
            owner="market",
            kind="quote",
            subject=subject,
            product="option",
            source="massive",
            start_time_unix_nanos=10_000_000,
            end_time_unix_nanos=20_000_000,
            parameters={
                "symbol": symbol,
                "instrument_id": f"instrument:{subject}",
                "credential_id": "massive-readonly",
            },
        )
        for subject, symbol in (("SPY-A", "O:SPY-A"), ("SPY-B", "O:SPY-B"))
    )
    calls: list[str] = []
    fail_second = True

    def run(_self, arguments):
        nonlocal fail_second
        values = list(arguments)
        symbol = values[values.index("--symbol") + 1]
        calls.append(symbol)
        if symbol == "O:SPY-B" and fail_second:
            fail_second = False
            raise RuntimeError("provider interruption")
        target = Path(values[values.index("--file") + 1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "Quote": {
                        "market_id": f"market:{symbol}",
                        "instrument_id": f"instrument:{symbol}",
                        "bid_price": "1",
                        "ask_price": "1.1",
                        "observed_at_unix_nanos": 15_000_000,
                        "source_id": "massive",
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return {"event_count": 1, "provider": "massive"}

    monkeypatch.setattr("kairospy.application.market.cli.MarketCliApplication.run", run)
    plan = asyncio.run(kairos.data.plan(requirements))
    with pytest.raises(RuntimeError, match="provider interruption"):
        asyncio.run(kairos.data.execute(plan))

    result = asyncio.run(kairos.data.execute(plan))
    journal = kairos.data.execution(plan.plan_hash)

    assert len(result.members) == 2
    assert calls == ["O:SPY-A", "O:SPY-B", "O:SPY-B"]
    assert journal["status"] == "complete"
    assert journal["steps"][0]["status"] == "reused"
    assert journal["steps"][0]["attempts"] == 1
    assert journal["steps"][1]["attempts"] == 2
    assert journal["result"]["composition_hash"] == result.composition_hash


def test_acquisition_uses_explicit_bounded_concurrency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    WorkspaceApplication().init_project(
        tmp_path / "project", workspace_id="concurrent-acquire"
    )
    kairos = Kairos.open(tmp_path / "project")
    requirements = tuple(
        DataRequirement(
            owner="market",
            kind="quote",
            subject=f"SPY-options/{symbol}",
            product="options",
            source="massive",
            start_time_unix_nanos=10_000_000,
            end_time_unix_nanos=20_000_000,
            parameters={
                "symbol": symbol,
                "instrument_id": f"instrument:{symbol}",
                "credential_id": "massive-readonly",
            },
        )
        for symbol in ("O:SPY-A", "O:SPY-B")
    )
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    active = 0
    maximum_active = 0

    def run(_self, arguments):
        nonlocal active, maximum_active
        values = list(arguments)
        symbol = values[values.index("--symbol") + 1]
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        try:
            barrier.wait(timeout=2)
            target = Path(values[values.index("--file") + 1])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(
                    {
                        "Quote": {
                            "market_id": f"market:{symbol}",
                            "instrument_id": f"instrument:{symbol}",
                            "bid_price": "1",
                            "ask_price": "1.1",
                            "observed_at_unix_nanos": 15_000_000,
                            "source_id": "massive",
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            return {"event_count": 1, "provider": "massive"}
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr("kairospy.application.market.cli.MarketCliApplication.run", run)
    plan = asyncio.run(kairos.data.plan(requirements))

    result = asyncio.run(kairos.data.execute(plan, max_concurrency=2))
    journal = kairos.data.execution(plan.plan_hash)

    assert len(result.members) == 2
    assert maximum_active == 2
    assert journal["max_concurrency"] == 2
    assert [step["status"] for step in journal["steps"]] == [
        "published",
        "published",
    ]


def test_failed_concurrent_acquisition_reuses_successful_sibling_on_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    WorkspaceApplication().init_project(
        tmp_path / "project", workspace_id="concurrent-resume"
    )
    kairos = Kairos.open(tmp_path / "project")
    requirements = tuple(
        DataRequirement(
            owner="market",
            kind="quote",
            subject=f"SPY-options/{symbol}",
            product="options",
            source="massive",
            start_time_unix_nanos=10_000_000,
            end_time_unix_nanos=20_000_000,
            parameters={
                "symbol": symbol,
                "instrument_id": f"instrument:{symbol}",
                "credential_id": "massive-readonly",
            },
        )
        for symbol in ("O:SPY-A", "O:SPY-B")
    )
    calls: list[str] = []
    fail_a = True

    def run(_self, arguments):
        nonlocal fail_a
        values = list(arguments)
        symbol = values[values.index("--symbol") + 1]
        calls.append(symbol)
        if symbol == "O:SPY-A" and fail_a:
            fail_a = False
            raise RuntimeError("temporary A failure")
        target = Path(values[values.index("--file") + 1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "Quote": {
                        "market_id": f"market:{symbol}",
                        "instrument_id": f"instrument:{symbol}",
                        "bid_price": "1",
                        "ask_price": "1.1",
                        "observed_at_unix_nanos": 15_000_000,
                        "source_id": "massive",
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return {"event_count": 1, "provider": "massive"}

    monkeypatch.setattr("kairospy.application.market.cli.MarketCliApplication.run", run)
    plan = asyncio.run(kairos.data.plan(requirements))

    with pytest.raises(RuntimeError, match="temporary A failure"):
        asyncio.run(kairos.data.execute(plan, max_concurrency=2))
    failed = kairos.data.execution(plan.plan_hash)

    assert failed["status"] == "failed"
    assert [step["status"] for step in failed["steps"]] == [
        "failed",
        "published",
    ]

    result = asyncio.run(kairos.data.execute(plan, max_concurrency=2))
    completed = kairos.data.execution(plan.plan_hash)

    assert len(result.members) == 2
    assert sorted(calls) == ["O:SPY-A", "O:SPY-A", "O:SPY-B"]
    assert completed["status"] == "complete"
    assert [step["attempts"] for step in completed["steps"]] == [2, 1]
    assert [step["status"] for step in completed["steps"]] == [
        "published",
        "reused",
    ]


def test_reviewed_massive_reference_plan_publishes_point_in_time_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    WorkspaceApplication().init_project(
        tmp_path / "project", workspace_id="reference-acquire"
    )
    kairos = Kairos.open(tmp_path / "project")
    as_of_nanos = 1_734_566_400_000_000_000
    requirement = DataRequirement(
        owner="reference",
        kind="option-contract",
        subject="SPY",
        product="options",
        source="massive",
        start_time_unix_nanos=as_of_nanos,
        end_time_unix_nanos=as_of_nanos,
        parameters={
            "underlying": "SPY",
            "as_of": "2024-12-19",
            "expiration_start": "2024-12-20",
            "expiration_end": "2025-01-31",
            "option_right": "put",
            "credential_id": "massive-readonly",
        },
    )
    invoked: list[list[str]] = []

    def run(_self, arguments):
        values = list(arguments)
        invoked.append(values)
        target = Path(values[values.index("--file") + 1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "kind": "option-contract",
                    "schema_version": "1",
                    "source_id": "massive",
                    "observed_at_unix_nanos": as_of_nanos,
                    "available_at_unix_nanos": as_of_nanos,
                    "as_of": "2024-12-19",
                    "instrument_id": "instrument:option:SPY:20241220:590:P",
                    "market_id": "market:massive:options:O:SPY241220P00590000",
                    "listing_id": "listing:massive:options:O:SPY241220P00590000",
                    "provider_symbol": "O:SPY241220P00590000",
                    "underlying_instrument_id": "instrument:equity:US:SPY:common",
                    "underlying": "SPY",
                    "expiry_unix_nanos": 1_734_652_800_000_000_000,
                    "strike": "590",
                    "option_right": "P",
                    "contract_multiplier": "100",
                    "status": "active-as-of",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return {
            "record_count": 1,
            "snapshot_id": "reference.option-contract/SPY/2024-12-19",
            "source": "massive",
        }

    monkeypatch.setattr(
        "kairospy.application.reference.cli.ReferenceCliApplication.run", run
    )

    plan = asyncio.run(kairos.data.plan((requirement,)))
    dataset_set = asyncio.run(kairos.data.execute(plan))

    assert invoked[0][0] == "prepare-option-contracts"
    assert "--api-key" not in invoked[0]
    assert dataset_set.members[0].owner == "reference"
    assert dataset_set.members[0].kind == "option-contract"
    assert (
        dataset_set.members[0].reference_snapshot_id
        == "reference.option-contract/SPY/2024-12-19"
    )
    assert asyncio.run(kairos.data.resolve((requirement,))) == dataset_set


def test_reviewed_massive_dividend_plan_uses_reference_data_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    WorkspaceApplication().init_project(
        tmp_path / "project", workspace_id="dividend-acquire"
    )
    kairos = Kairos.open(tmp_path / "project")
    start = 1_704_067_200_000_000_000
    end = 1_735_603_200_000_000_000
    requirement = DataRequirement(
        owner="reference",
        kind="cash-dividend",
        subject="SPY",
        product="equity",
        source="massive",
        start_time_unix_nanos=start,
        end_time_unix_nanos=end,
        parameters={
            "ticker": "SPY",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "credential_id": "massive-readonly",
        },
    )
    invoked: list[list[str]] = []

    def run(_self, arguments):
        values = list(arguments)
        invoked.append(values)
        target = Path(values[values.index("--file") + 1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "kind": "cash-dividend",
                    "schema_version": "1",
                    "source_id": "massive",
                    "observed_at_unix_nanos": 1_710_460_800_000_000_000,
                    "available_at_unix_nanos": 1_709_164_800_000_000_000,
                    "dividend_id": "div-1",
                    "instrument_id": "instrument:equity:US:SPY:common",
                    "ticker": "SPY",
                    "ex_dividend_date": "2024-03-15",
                    "declaration_date": "2024-02-29",
                    "record_date": "2024-03-18",
                    "pay_date": "2024-04-30",
                    "cash_amount": "1.59",
                    "split_adjusted_cash_amount": "1.59",
                    "historical_adjustment_factor": None,
                    "currency": "USD",
                    "distribution_type": "CD",
                    "frequency": 4,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return {"record_count": 1, "source": "massive"}

    monkeypatch.setattr(
        "kairospy.application.reference.cli.ReferenceCliApplication.run", run
    )

    plan = asyncio.run(kairos.data.plan((requirement,)))
    dataset_set = asyncio.run(kairos.data.execute(plan))

    assert invoked[0][0] == "prepare-dividends"
    assert "--api-key" not in invoked[0]
    assert dataset_set.members[0].owner == "reference"
    assert dataset_set.members[0].kind == "cash-dividend"
    assert dataset_set.members[0].reference_snapshot_id == (
        "reference.cash-dividend/SPY/2024-01-01/2024-12-31"
    )
    assert asyncio.run(kairos.data.resolve((requirement,))) == dataset_set
