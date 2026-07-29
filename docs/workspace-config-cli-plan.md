# Workspace, Configuration, and CLI Plan

This document defines how KairosPy should organize local project configuration, workspace state, run configuration files, accounts, orders, reference catalogs, and historical data behind one coherent CLI.

## Decision

KairosPy should not move every configuration file into `.kairos`.

Use three separate configuration/state planes:

1. `.kairos/kairos.toml` is the local project workspace manifest.
2. `configs/runs/*.toml` and user-supplied run config paths are declarative run specifications.
3. `.kairos/` is the local workspace state and artifact directory.

The CLI should resolve those planes through one workspace resolver, then expose commands around domain objects such as runs, accounts, orders, reference data, and historical data.

Create a local project with:

```bash
kairospy init my-project
cd my-project
```

This creates:

```text
my-project/
  .kairos/
    kairos.toml
    accounts/
    state/
    runs/
    data/
    reference/
    orders/journals/
```

## Goals

- Make configuration predictable and inspectable.
- Preserve support for user-defined run config files outside the repository.
- Keep `.kairos` from becoming a hidden source of business configuration.
- Give CLI commands a shared workspace context instead of each command guessing paths.
- Make live trading, paper trading, backtesting, account inspection, order control, reference lookup, and historical data management feel like one system.

## Non-Goals

- Do not require all user run configs to live under the repository.
- Do not store secrets in `.kairos/kairos.toml` or run configs; account credentials belong only in `.kairos/accounts`.
- Do not make run history the source of truth for future runs.
- Do not force every command to accept low-level paths when a registered object name is available.

## Configuration Planes

### Workspace Manifest: `.kairos/kairos.toml`

`.kairos/kairos.toml` describes local project defaults and available capabilities. It is created by `kairospy init project-name` and is part of the machine-local workspace state.

Recommended responsibilities:

- Project identity and timezone.
- Default workspace paths.
- Data and reference defaults.
- Provider declarations.
- Shared provider declarations.
- CLI defaults.
- Global safety defaults.

Examples:

```toml
schema_version = 1

[project]
name = "kairospy"
timezone = "UTC"

[paths]
workspace_root = ".kairos"
lake_root = ".kairos/data"
run_root = ".kairos/runs"
reference_root = ".kairos/reference"

[providers.binance]
type = "exchange"
enabled = true
```

`.kairos/kairos.toml` should not contain per-run tactical details such as a specific strategy's symbol list, one backtest window, or a one-off risk override.
It should also not contain trading account credentials. Those belong in `.kairos/accounts`.

The manifest is a small declaration of how this folder behaves as a Kairos workspace. It is not a database and not an audit log.

Good manifest contents:

- Workspace identity: project name and timezone.
- CLI preferences: output format, language, run-control defaults.
- Path conventions only when the defaults are not enough.
- Storage defaults such as historical data format.
- Optional provider feature toggles that are not account-specific.

Bad manifest contents:

- API keys or account secrets.
- Account inventories.
- Registered run specs.
- Active selections.
- Daemon state.
- Historical operation records.
- Run logs, order logs, account snapshots, or reference snapshots.

Those mutable records should live in dedicated state files or journals:

```text
.kairos/state/run-index.json        # registered run specs
.kairos/state/selection.json        # current UI/CLI selection
.kairos/state/operations.jsonl      # optional CLI operation history
.kairos/runs/                       # run instances, daemon state, logs, summaries
.kairos/orders/journals/            # order command journals
.kairos/accounts/                   # account config
.kairos/accounts/journals/          # account snapshots/journals
.kairos/reference/                  # reference cache/snapshots
.kairos/data/                       # historical data
```

In short: `.kairos/kairos.toml` configures the CLI/workspace; `.kairos/state` and product journals record what happened.

### Run Configs: `configs/runs/*.toml` and Custom Paths

A run config describes one runnable intention. It should be portable, reviewable, and reproducible.

Recommended responsibilities:

- Run identity and mode.
- Strategy import path.
- Account reference.
- Market or universe selection.
- Mode-specific execution settings.
- Run-specific risk limits.
- Backtest time ranges and replay settings.
- Paper/live venue and symbol settings when they are specific to the run.

Example:

```toml
[run]
id = "binance-btc-paper"
mode = "paper"
strategy = "examples.strategies.sma:strategy"

[account]
ref = "binance_testnet_spot"

[market]
venue = "binance"
market = "spot"
symbol = "BTC/USDT"

[execution]
dry_run = true
max_order_notional = "100"
```

Run config files can live in:

- `configs/runs/*.toml` for reusable run specs.
- `examples/configs/*.toml` for examples.
- Any user path, including files outside the repository.

The workspace should register custom paths instead of copying them into `.kairos` by default.

### Local Workspace: `.kairos/`

`.kairos/` is local operational state. It can be recreated, migrated, or pruned without changing the project's declarative intent.

Recommended structure:

```text
.kairos/
  state/
    workspace.json
    run-index.json
    selection.json

  runs/
    backtest/
      <run-id>/
        instances/
          <run-instance-id>/
    paper/
      <run-id>/
        instances/
          <run-instance-id>/
    live/
      <run-id>/
        instances/
          <run-instance-id>/

  data/
    market/
    reference/

  reference/
    reference.sqlite
    snapshots/

  accounts/
    snapshots/
    journals/

  orders/
    journals/

  profiles/
    local.toml
```

Recommended responsibilities:

- Run registry state.
- Active run selection.
- Daemon state, heartbeat, stop commands, logs, and summaries.
- Daemon run groups live at `.kairos/runs/<mode>/<run-id>`; launch-specific artifacts live under `instances/<run-instance-id>`.
- Historical data lake.
- Reference catalog caches.
- Account snapshots and journals.
- Order journals.
- Local user profiles.

`.kairos` should not be the default home for shared run config files. The exception is local-only profile configuration, which should be ignored by git.

Trading account credentials belong under `.kairos/accounts`, one account per TOML file:

```toml
# .kairos/accounts/binance_live_spot.toml
[account]
id = "binance_live_spot"
provider = "binance"
environment = "live"
venue = "binance"
market = "spot"
currency = "USDT"

[credential]
kind = "api_key_secret"
api_key = "..."
api_secret = "..."
ip_bound = true

[permissions]
account_read = true
order_write = true
order_cancel = true
```

Paper and live run configs must reference these accounts with `[account].ref`. Inline `[accounts]`, `[broker]`, and `[credentials]` tables are not part of the run-config model.

## Registry Instead of Config Relocation

To support both repository run configs and arbitrary user files, `.kairos` should keep indexes that point to source config files.

Example `.kairos/state/run-index.json`:

```json
{
  "schema_version": 1,
  "default_run": "hl-paper",
  "runs": {
    "hl-paper": {
      "config": "configs/runs/hyperliquid-paper.example.toml",
      "registered_at": "2026-07-29T00:00:00Z",
      "last_instance": "20260729T000000Z"
    },
    "private-live": {
      "config": "/Users/me/kairos-runs/private-live.toml",
      "registered_at": "2026-07-29T00:00:00Z"
    }
  }
}
```

The registry should store metadata and pointers. The source TOML file remains the authority for the run's declared behavior.

## Configuration Precedence

The resolver should apply configuration in this order:

```text
built-in defaults
  -> local workspace manifest .kairos/kairos.toml
  -> user profile, such as .kairos/profiles/local.toml
  -> run config
  -> CLI flags
```

Rules:

- Later layers may override defaults from earlier layers.
- Run configs should reference accounts by stable IDs.
- CLI flags should be tactical overrides, not a separate long-term configuration system.
- Effective config should be explainable by the CLI.

Suggested command:

```bash
kairospy config explain --run hl-paper
kairospy config explain --config configs/runs/hyperliquid-paper.example.toml
```

The output should show source attribution for important fields:

```text
run.id                  hl-paper                                  configs/runs/hyperliquid-paper.example.toml
account.ref             hyperliquid_paper_perp                    configs/runs/hyperliquid-paper.example.toml
paths.run_root          .kairos/runs                              .kairos/kairos.toml
account.source          .kairos/accounts/hyperliquid_paper_perp.toml
```

## Workspace Resolver

Add one application-level resolver used by every surface command.

Conceptual API:

```python
@dataclass(frozen=True, slots=True)
class KairosWorkspace:
    root: Path
    manifest_path: Path | None
    manifest: KairosConfig
    state_root: Path
    run_root: Path
    data_root: Path
    reference_root: Path
    run_index: RunIndex
    account_registry: AccountRegistry
```

Responsibilities:

- Find the project root by walking up to `.kairos/kairos.toml`.
- Load `.kairos/kairos.toml` if present.
- Resolve `.kairos` paths.
- Load local profile config if present.
- Open run/account/order registries.
- Provide consistent path defaults to all CLI products.

Existing commands such as `run list`, `run daemon`, `data download`, `reference`, `account`, and `order` should receive their roots from this workspace object unless the user explicitly overrides them. `broker` should remain an internal adapter boundary, not a primary CLI product.

## CLI Information Architecture

The CLI should be organized around product nouns, not file layout.

Proposed top-level commands:

```text
config       workspace manifest, profiles, effective config, diagnostics
run          run specs, run instances, daemons, logs, status
account      configured accounts, balances, open orders, snapshots
order        place, cancel, replace, list, inspect orders
reference    reference catalogs and lifecycle data
data         historical data download, inspect, read, replay, prune
stream       live market/account stream utilities
integration  provider and exchange diagnostics
strategy     strategy utilities
```

### Config Commands

```bash
kairospy config show
kairospy config paths
kairospy config doctor
kairospy config operations
kairospy config explain --run hl-paper
kairospy config profile list
kairospy config profile use local
```

### Run Commands

`run` should manage both run specs and run instances.

```bash
kairospy run register hl-paper configs/runs/hyperliquid-paper.example.toml
kairospy run unregister hl-paper
kairospy run specs
kairospy run validate hl-paper
kairospy run explain hl-paper

kairospy run start hl-paper
kairospy run stop hl-paper
kairospy run status hl-paper
kairospy run list
kairospy run logs hl-paper
kairospy run artifacts hl-paper
```

Custom path commands remain valid because custom run files are first-class run specs:

```bash
kairospy run start /Users/me/private/live.toml
kairospy run validate configs/runs/backtest.example.toml
```

### Account Commands

Accounts are system resources. Account config should live under `.kairos/accounts`, while account snapshots and journals live under `.kairos/accounts/snapshots` and `.kairos/accounts/journals`.

```bash
kairospy account list
kairospy account create binance_live_spot --provider binance --environment live --market spot --currency USDT --credential-kind api_key_secret
kairospy account show binance_testnet_spot
kairospy account balance binance_testnet_spot
kairospy account open-orders binance_testnet_spot
kairospy account snapshot binance_testnet_spot
kairospy account doctor binance_testnet_spot
```

### Order Commands

Order commands should operate on account references. They should require explicit confirmation or safety controls for live accounts.

```bash
kairospy order open --account binance_testnet_spot
kairospy order place --account binance_testnet_spot --symbol BTC/USDT --side buy --type limit --qty 0.01 --price 50000
kairospy order cancel --account binance_testnet_spot --order-id abc123
kairospy order show --account binance_testnet_spot --order-id abc123
```

Live order placement should pass through a preflight step:

```bash
kairospy order place --account binance_live_spot --symbol BTC/USDT --side buy --type limit --qty 0.01 --price 50000 --confirm-live
```

### Reference Commands

`reference` owns both catalog maintenance and user-facing lookup for symbols, instruments, listings, and markets.

```bash
kairospy reference search BTC
kairospy reference show binance:spot:BTC/USDT
kairospy reference resolve BTC/USDT --venue binance --market spot
kairospy reference refresh --provider massive
kairospy reference markets --active-only
kairospy reference catalog status
```

### Data Commands

Historical data should keep using the workspace data root by default.

```bash
kairospy data download --symbol BTC/USDT --exchange binance --timeframe 1m
kairospy data list
kairospy data inspect market.ohlcv.binance_spot_btc_usdt.1m
kairospy data alias market.ohlcv.binance_spot_btc_usdt.1m btc-bars
kairospy data read market.ohlcv.binance_spot_btc_usdt.1m --limit 10
kairospy data replay market.trades.binance_spot_btc_usdt --speed 0
kairospy data prune market.ohlcv.binance_spot_btc_usdt.1m --start 2024-01-01T00:00:00+00:00 --end 2025-01-01T00:00:00+00:00
```

## Safety Model

Trading commands should treat live side effects differently from reads and simulations.

Recommended rules:

- Account read commands are allowed when the `.kairos/accounts` entry has the required permission.
- Paper and backtest order commands can run without live confirmation.
- Live order placement requires explicit account environment `live`.
- Live order placement requires a safety gate such as `--confirm-live`.
- Live order placement should support a dry-run rendering mode.
- Every order command should write an order journal record under `.kairos/orders` or the relevant run directory.
- Cancel commands should show the resolved account, venue, symbol, and order ID before execution in interactive mode.

## File Ownership

Recommended git policy:

```text
Commit:
  configs/runs/*.toml
  docs/*.md
  examples/configs/*.toml

Ignore:
  .kairos/
  .env
  local profile files with secrets or machine-specific paths
```

If a project wants to share `.kairos` defaults, put them in a normal tracked template instead:

```text
configs/profiles/local.example.toml
configs/workspace/defaults.toml
```

Then provide a setup command:

```bash
kairospy init project-name
kairospy config profile create local --from configs/profiles/local.example.toml
```

## Migration Path

### Phase 1: Normalize Concepts

- Document the three planes: workspace manifest, run config, local workspace state.
- Rename user-facing help text to use `workspace`, `run spec`, and `run instance` consistently.
- Keep existing non-conflicting command behavior working.

### Phase 2: Add Workspace Resolver

- Add `KairosWorkspace`.
- Route `run`, `data`, `reference`, `account`, and `order` commands through it.
- Remove hard-coded `.kairos/runs` defaults from command bodies where possible.
- Add `kairospy config paths` and `kairospy config doctor`.

### Phase 3: Add Run Index

- Add `.kairos/state/run-index.json`.
- Add `run register`, `run unregister`, and `run specs`.
- Allow `run start NAME` and `run validate NAME`.
- Keep custom run spec paths as first-class inputs.

### Phase 4: Split Account and Order Surfaces

- Move account inspection into `account`.
- Remove `broker` from the primary CLI surface; broker adapters remain runtime/integration internals.
- Add order commands around account references.
- Add order journal records.

### Phase 5: Improve Effective Config Introspection

- Add source-aware effective config rendering.
- Show which layer supplied each important value.
- Add validation that catches conflicting project/run/profile definitions.

## Naming Decisions

Use these names consistently:

| Name | Meaning |
| --- | --- |
| Workspace | The local Kairos project context resolved from the current directory. |
| Workspace manifest | `.kairos/kairos.toml`. Local project defaults for one Kairos project folder. |
| Profile | User-local configuration layer. Machine-specific and usually untracked. |
| Run spec | A TOML file describing one runnable intention. |
| Run instance | One execution of a run spec, with logs, state, and artifacts. |
| Run index | Workspace registry mapping names to run spec paths and metadata. |
| Account ref | Stable configured account ID used by run and order commands. |
| Account credential | Credential fields stored with a local account file under `.kairos/accounts`. |

## Summary

The mature shape is not "put every config under `.kairos`." The mature shape is:

- Shared or reusable run intent lives in explicit run TOML files.
- Local project configuration and operational state live in `.kairos`.
- Custom user files remain first-class through registries and path resolution.
- Every CLI command uses one workspace resolver.
- The CLI is organized around trading system concepts, not around directories.

This keeps KairosPy flexible for individual workflows while still giving the project a stable, inspectable operating model.
