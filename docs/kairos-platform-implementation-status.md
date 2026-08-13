# Kairos Platform Architecture Implementation Status

This file is the evidence index for implementing
`kairos-platform-kairospy-research-architecture.md`. It records verified
current state, not intended state. A row is complete only when its evidence
proves the corresponding architecture requirement.

Last updated: 2026-08-14.

## Status meanings

- `complete`: implemented and covered by direct evidence.
- `partial`: an aligned implementation exists, but required behavior remains.
- `missing`: no implementation evidence yet.
- `blocked`: externally blocked after the required repeated audit; none at present.

## Shared-path architecture

The governing invariant is **shared path, semantic fork**: callers converge on
one authoritative model and Application path before any fork; a fork is
allowed only for a real consumption or Composition difference and must retain
the same identity, facts, lineage and audit contract.

| Requirement | Status | Evidence / remaining work |
|---|---|---|
| Project root and `.kairos` resolve to the same runtime context | complete | `WorkspaceApplication.open()` and `test_kairos_data_client_binds_all_operations_to_explicit_project` |
| Multiple Projects isolate catalogs and state | complete | `tests/test_data_architecture.py` opens two Projects and proves data is not visible across them |
| TOML and typed `BacktestSpec` share one canonical config and `LaunchPlan` | complete | `test_toml_and_backtest_spec_share_one_canonical_launch_plan` |
| CLI and Python run through one Launch Application | complete | `LaunchRuntimeApplication` owns instance identity, leases, component Composition, Replay barrier, lifecycle, cleanup and report access. CLI is an adapter and `BacktestsClient.run()` invokes the same Application; `test_cli_and_python_backtest_use_the_same_launch_runtime_application` proves both entry paths converge |
| CLI and Python share one Data Application | complete | Root `kairos data list/inspect/plan/execute/execution/set/gate` is an adapter over the same `Kairos.open(Project).data` applications used by Research. Execution accepts the reviewed `--expected-plan-hash` and rejects changed requirements, while journal lookup is independently available. Capability-level lazy dispatch lets `kairos data` run without importing Launch or Strategy runtime; a subprocess test proves this isolation even while the Strategy package is mid-refactor. Focused tests prove plan/execute, named sets and persisted Gate evidence share the Catalog and journal; old Market commands remain owner/integration capabilities rather than a second unified Catalog |
| CLI and Python share one Research Application | complete | `ResearchSpec.from_dict()` reconstructs the same hash-stable public plan contract persisted by Python. `kairos research plan lock/show` and `research gate publish/show` are thin adapters over `Kairos.open(Project).research`; capability-level lazy dispatch and subprocess coverage prove this path does not import Launch or the Strategy runtime. No second Research runner or evidence store exists |
| Backtest, Paper and Live differ only at Composition | partial | All modes now share `LaunchRuntimeApplication`; remaining work is to finish the documented option-specific Risk/Execution/Account behavior and prove mode-parity gates |

## Business transport Gate 0

The evidence and remaining work are tracked in
[`business-transport-contract-audit.md`](./business-transport-contract-audit.md).

| Requirement | Status | Evidence / remaining work |
|---|---|---|
| Workspace-isolated Aeron runtime | complete | System, business publishers and Strategy bridge sources resolve the same `.kairos/run/aeron/media`; focused process/composition tests prove the binding |
| Aeron and mmap business payloads use FlatBuffers | complete | All 21 active Account, Execution, Market and Risk roots have validated identifiers and shared Rust/Python compatibility fixtures |
| Strategy Market contract matches current need | complete | Quote, Bar, Trade and Greeks are typed public events; other observations only advance continuity. Live dispatch filters by the Strategy instance's requested market, selector and Bar timeframe |
| Direct service-to-contract mapping | complete | Account, Market, Risk and Execution explicitly map application/domain models into contract-owned types; an architecture test rejects JSON model adapters in active publisher composition |
| Event gap and resume contract | complete for Gate 0 | All four application streams detect gaps, never infer recovery from mmap, and drive Strategy to `FAILED`; durable retention/resync is explicitly outside the first fail-closed contract |
| Snapshot metadata semantics | complete | All active roots use schema version 1, separate state generation, publication time and business-derived as-of time; zero as-of means an empty/unknown image. Production encoder tests decode and assert these fields for Account, Execution, Market and Risk |

## Unified data architecture

| Requirement | Status | Evidence / remaining work |
|---|---|---|
| Fine-grained immutable atomic Dataset | complete | `DatasetCatalogApplication.publish()`, physical SHA-256 verification and immutability tests. Public `data.describe()` exposes the logical Ref, lineage, quality report and partition summaries while hiding physical paths |
| Storage-independent `DatasetRef` and `DatasetSetRef` | complete | Public refs contain logical identity/version/hash and no physical path |
| Dataset composition hash | complete | `DatasetSetRef` deterministically computes and verifies composition identity |
| Named Dataset Set persistence and movable Alias | complete | `pin_set()` stores immutable compositions by hash and moves only the explicit Project alias; `load_set()` can resolve either current Alias or a historical Composition Hash. Compare-and-swap prevents concurrent alias overwrite. The real two-leg Quote smoke composition is pinned as `spy-put-spread-smoke` and retains its passed Gate 1 report |
| Read-only `resolve()` with structured missing coverage | complete | `MissingCoverage`, `DataUnavailableError` and focused tests |
| Reviewed `plan()` before writes | complete | `DataAcquisitionPlan` is deterministic and read-only |
| Explicit `execute()` and opt-in `ensure(acquire_missing=True)` | complete | Massive Market Quote/Trade/Bar and Point-in-time Reference Option Contract/Cash Dividend execution are real-data verified. A durable per-plan journal records attempts/progress/failure/result; every retry resolves each Requirement before provider access and reuses completed steps. `OptionMarketPreparationApplication` deterministically expands selected Reference contracts into fine-grained per-contract/per-window Requirements, so large option preparations use the same reviewed plan and resume semantics instead of a second downloader |
| Shared `DatasetReadPlan` | complete | Snapshot and Replay use the exact same read plan and fact ordering |
| Snapshot/Replay fact-set parity | complete | Contract tests prove complete Replay consumption equals Snapshot facts |
| Replay cursor/checkpoint/materialization identity | complete | Checkpoint is bound to read-plan hash; compatibility materialization writes the Dataset Set and ReadPlan identity |
| Analytical Projection and lazy Polars/DuckDB scan | partial | `DatasetAnalyticalView` lazily scans internal partitions into a Polars `LazyFrame` using the same bounded `DatasetReadPlan`, enforces the ReadPlan kind filter, and provides a backward as-of Point-in-time Join over identity and availability time without decoding numeric values. Market's deterministic `black-scholes-european-v1` Application derives IV/Delta/Gamma/Vega/Theta with complete time/model/Reference lineage. `publish_derived()` now forces every derived Dataset to bind its exact parent members/Composition Hash, derivation, availability semantics and—for option Greeks—Reference Snapshot. Numeric projection execution remains to be connected after the lower numeric representation boundary is stable; no mantissa/scale decoder is duplicated here |
| Full Point-in-time Reference Dataset | complete | Reference-owned Massive acquisition publishes immutable `option-contract` and `cash-dividend` datasets with canonical IDs, observed/available time and `reference_snapshot_id`. The real-data gate includes 105 weekly SPY decision-date snapshots across 2024–2025 plus 9 cash dividends for 2023-12-01 through 2026-02-15 |
| Deterministic preparation, partitioning and atomic multi-part publish | complete | One immutable Dataset can contain deterministically ordered internal partitions while retaining one logical `DatasetRef`. Rename+Catalog commit is serialized, concurrent identical publishers converge, and a crash between data rename and Catalog update is recovered by validating and registering the orphaned version. New acquisitions partition by event date; legacy single-file manifests remain readable |

## Massive real-data evidence

The Workspace credential `.kairos/credentials/massive-readonly.toml` is read
only by Workspace/Integration code. Its filesystem mode was tightened from
`0644` to `0600`. Secret values are never included in plans, manifests,
commands, reports, or this document.

Verified provider path:

```text
DataRequirement
→ DataAcquisitionPlan
→ DataAcquisitionApplication
→ MarketCliApplication / ReferenceCliApplication
→ Market / Reference owner application
→ Integration Massive capability
→ Workspace credential resolver
→ Massive REST
→ Market Quote/Trade/Bar or Reference Option Contract/Cash Dividend normalization
→ quality validation
→ immutable Dataset publication
→ DatasetSetRef
```

Real calls verified on 2026-08-13:

| Probe | Result |
|---|---|
| `O:SPY241220P00590000`, historical quotes, 60 seconds | 471 real Quote observations; zero crossed/negative/zero-sided quotes in the probe |
| Same contract, historical trades, one trading day | 8,000 real Trade observations; zero non-positive price/quantity observations |
| Reviewed plan execution, historical quotes, 10 seconds | 104 observations published through the complete `plan → execute → DatasetSetRef` path |
| SPY Point-in-time put contract catalog, `as_of=2024-12-19`, expiries 2024-12-20 through 2025-01-31 | 1,794 unique contracts, zero wrong-underlying/right records; published as `reference.option-contract/SPY/829902d90b87bbf1b57b@0fe7efb7e9f04fc2` with Composition Hash `4755cda8fe7b7ca33db83b138226385865f17f8d30cacae533673ec2977fc1d0` |
| SPY adjusted and unadjusted daily bars, 2023-12-01 through 2026-02-15 | 552 observations in each immutable Dataset; published as `market.bar/SPY-adjusted/bc75ad063fa86a7733f1@13e7149428a76d80` and `market.bar/SPY-unadjusted/c163ab05f4164b8714fd@0ef14f725c5f7fc5` |
| SPY cash dividends, ex-dates 2023-12-01 through 2026-02-15 | 9 Reference-owned records with declaration/ex/record/pay dates and cash-adjustment fields; published as `reference.cash-dividend/SPY/b11d41e37dfa2b4f5d95@b32417740020e48e` with Composition Hash `af906146324a9430a00c8fdb6b6661d19de530c6cd0a676bd55261efec257b61` |
| Weekly SPY Point-in-time put catalogs, 2024-01-03 through 2025-12-31 | 105/105 immutable Reference Datasets published, each restricted at provider query time to the decision date, Put right and 30–45 DTE expiry interval; the preparation is resumable through Catalog resolution |
| Multi-contract option Quote preparation smoke gate | A reviewed two-step plan downloaded independent 10-second Quote windows for `O:SPY241220P00580000` (49 facts) and `O:SPY241220P00590000` (87 facts) through `massive-readonly`, published them as two immutable per-contract Datasets, and completed journal `c738a4a025c8ae0b5f14f3246bc41c534fed9fe1d129e4a21f8cf9632d74dfdc`. Replanning resolved both locally in about 5ms with no acquisition steps |
| Bounded concurrent option acquisition smoke gate | With explicit `max_concurrency=2`, plan `f6a3084c531b1ff71f6ae0d42d8dfb58e703b5d3d917e60319292bdd194dfe75` downloaded new 570P and 600P windows concurrently in 9.7 seconds, published 14 and 18 validated Quote facts, and recorded both steps as published. Default concurrency remains 1; a focused barrier test proves the bound and journal evidence |
| Persisted real-data Gate 1 smoke report | Point-in-time DatasetSet Composition Hash `b9675e6f91ea9fe27a5eaa336238e1128b1b550c913accbd5c15fcd691937f28` passed all Manifest integrity, Lineage, Quality, required-Kind and composition-policy checks; evidence is persisted under the Project's data gate state |
| January 2025 longitudinal Quote preparation | Reviewed plan `8ac5ba54ef604ba89c6f21841d6a5afba9264931a632e4ff652e9021e5525bb8` requested 36 independent 60-second Quote windows across four Wednesdays, one nearest-37-DTE expiry per date and strikes 500–660. Massive returned and the Catalog atomically published 32 Datasets with 2,304 real Quotes; four empty windows were rejected and retained as failed journal steps instead of publishing false coverage |
| January 2025 liquid-grid Dataset Set Gate 1 | The explicitly narrowed 520–620 grid resolves 24/24 Quote members with 1,874 facts. Combined with four Point-in-time option-contract snapshots, unadjusted SPY bars and cash dividends, alias `spy-options-2025-01-liquidity-smoke` pins 30 immutable members at Composition Hash `48518df8e76ef945830a2d859300c29c903886704ddf33a244548247d23da2ea`; persisted Gate 1 passes required Kind, Manifest, Lineage, Quality and PIT checks |

The provider adapter uses the official historical endpoints documented by
Massive for options quotes and trades. The configured private endpoint uses
Bearer authentication; HTTP must not be used because an HTTP-to-HTTPS redirect
can remove the Authorization header.

The current Catalog contains 106 SPY Point-in-time contract Datasets with 44,550
contract facts, 38 Quote Datasets (the original probes plus the 32 successful
January windows), and one Trade probe with 8,000 facts. The four-date January
slice proves longitudinal preparation and quality behavior, but is not sufficient
for the final 24-month study.
The platform now prepares deterministic, deduplicated per-contract/per-window
Quote/Trade/Bar Requirements, each with an independent atomic subject and durable
resume step. Remaining Massive work is to execute the reviewed longitudinal target
set and publish its formal aggregate quality evidence. Massive's adjusted bar flag is not
treated as a dividend total-return series; Research must derive returns by
combining Market-owned bars with Reference-owned cash-dividend facts.

## SPY option trading semantics

| Requirement | Status | Remaining evidence |
|---|---|---|
| Point-in-time deterministic option selection | partial | The public `OptionSpreadSelectionApplication` consumes canonical Reference/Quote/Greeks candidates, enforces availability/observation time, DTE, Put right, quote freshness/validity and a lower-strike protection leg, applies explicit deterministic tie-breakers, and preserves every rejection reason. The Reference PIT Dataset is complete; automatic construction of joined candidates remains behind the pending Dataset analytical numeric boundary |
| Multi-leg intent | partial | Execution already owns generic explicit legs and intent lifecycle. The public SDK now adds a fixed-risk `OptionSpreadRequest` that requires one Sell short leg, one Buy protection leg, equal quantity, maximum loss, minimum credit and All-or-Nothing/Cancel-Remaining semantics. Runtime transport and Execution-owned option-specific validation are not yet connected |
| Conservative leg/combo fills | missing | Existing simulator does not yet prove the document's SPY spread semantics |
| Multiplier, fees, positions and PnL | partial | Foundational Account/Execution behavior exists; complete option-spread E2E evidence is absent |
| Expiry/assignment restrictions | partial | `OptionBacktestConstraints` is part of the canonical Launch config used equally by TOML and typed `BacktestSpec`. The supported first slice rejects expiry holding, assignment/exercise, naked options, 0DTE, dynamic Delta hedging, dividend-window exposure, non-package fills and more than one open spread; it emits the mandatory report limitations. Runtime exit enforcement and E2E evidence remain |
| Canonical report and traceability | partial | `CanonicalBacktestReportApplication` validates both baseline-conservative and stress-cost scenarios, all required performance/execution metrics, semantic limitations and the complete Decision/Market/Reference/Intent/Risk/Order/Fill/Account/Position/PnL identity chain. It accepts the exact canonical `OptionBacktestConstraints`, automatically includes their limitations, and binds them into the deterministic DatasetSet/config/seed/code/scenario/trace hash while excluding runtime instance identity. Runtime owners still need to emit and aggregate these facts |

## Required final deliverables

| Deliverable | Status | Exit evidence |
|---|---|---|
| Independent SPY option Research repository | missing | Reproducible study, real DatasetSetRef, PIT sampling, quality report, holdout/bootstrap/cost analysis and final report |
| Independent SPY put-spread Strategy repository | missing | Public-SDK-only strategy, unit/contract/execution/E2E tests and deterministic Canonical Backtest report |
| Four architecture Gates | partial | Gate 1 has a tested, persisted `DataTrustGateApplication` that verifies required atomic kinds, point-in-time composition policy, every member's physical Manifest hash/count, Lineage, Quality and PIT Reference identity. It now also scans option Market facts across members and requires every Quote/Trade/Greeks instrument to match a Reference contract available no later than the market observation. Gate 2 freezes the hypothesis, exact DatasetSet, ordered non-overlapping Holdout, PIT feature/label rules, baseline, seed/code version, Quote staleness, all four cost scenarios, parameter space, maximum trials, concurrency and selection rule; Holdout parameter changes are structurally rejected. Gate publication requires per-split sample/missing/Bootstrap evidence and an explicit conclusion. Real SPY Research evidence and Gates 3–4 remain |

Research is now explicitly a researcher-facing API facade, not a runtime.
`kairos.research.data` returns an equivalent Project-scoped `DataClient` as
`kairos.data`; `kairos.research.launches` returns the same canonical
`LaunchesClient` as `kairos.launches`; and `kairos.research.backtests` returns the
typed Backtest convenience facade over Launch + Config. `BacktestsClient.run_many`
supports pre-registered parameter cases with unique Case/Launch IDs, an explicit
concurrency bound, stable result ordering and per-case failure isolation. Research
norms remain owned by `ResearchSpec`, immutable plan locking and Gate 2. There is
deliberately no Research runner, Host, Context, Process, instance or Launch mode.
The facade is deliberately limited to three responsibilities: convenient Data
operations, convenient Launch operations, and enforceable Research norms. It does
not become a Data or runtime owner.

## Current verification baseline

- New data and launch configuration focused suite: 25 tests passed before the
  shared runtime extraction.
- CLI/data/launch focused suite: 75 tests passed after extracting
  `LaunchRuntimeApplication` and exposing `BacktestsClient.run()`.
- Massive Integration includes a focused bounded/authenticated dividend-path
  test. Data architecture, lazy projection and Market analytics focused tests
  pass, including partition atomicity, concurrent convergence, interrupted
  commit recovery, execution resume, existing-manifest compatibility and
  deterministic IV/Greeks derivation.
- The current data/analytics/selection/spread/report focused suite passes 29 tests; the
  Dataset architecture suite independently passes 19 tests, including the
  storage-independent lineage and quality description entry.
- Ruff and Pyright pass for the new Python data/launch surfaces.
- `cargo fmt --all -- --check` passes after formatting.
- The current full Python run passes with 335 tests passed and 8 skipped.
- `cargo test --workspace` passes, including doctests, when run with an isolated
  Cargo target directory. The isolation avoids artifact races with concurrent
  local Cargo processes. Workspace process locks now write ownership through the
  already-locked descriptor and explicitly release `flock` on drop, so same-process
  rejection/reacquisition and cross-process exclusion are deterministic on macOS.
- Component startup now checks child termination before transport readiness and
  only probes health after the Unix socket exists. The full suite proves the
  early-exit diagnostic remains below its two-second contract under load.
- The currently active Strategy runtime redesign remains a protected workstream.
  Data capability loading is correctly independent of Backtest/Launch/Strategy runtime loading, so
  Research/Data verification does not import that process stack. The complete
  Data architecture suite passes 29 tests, and Data CLI isolation/parity adds 2
  focused tests; Project-bound Research Gate and Research CLI coverage bring the
  independent Data/CLI/Research suite to 45 tests, including capability-level
  isolation, immutable pre-Holdout plan locking, hash-stable plan round trips,
  fixed parameter-search/concurrency/Holdout rules, shared Python/CLI Gate
  persistence, canonical Launch facade delegation, bounded multi-Launch execution,
  stable case ordering and failure isolation, as well as
  Gate 1, Point-in-time Join, multi-contract preparation, bounded concurrency
  and explicit rejection of empty provider coverage. The real January Dataset Set
  was re-evaluated under the cross-member PIT identity rule: 1,874/1,874 option
  Quote facts matched one of 687 Reference instruments available at observation
  time, with 100% identity/time completeness. Data/Launch/report focused
  verification passes 48 tests. The current Strategy runtime worktree changes were
  preserved during baseline recovery.

This objective is not complete while any required row remains `partial` or
`missing`.
