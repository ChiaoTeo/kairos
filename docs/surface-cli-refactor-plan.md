# Surface CLI Refactor Plan

This document defines the target architecture for `kairospy.surface`.
It is a clean-cut refactor plan: no compatibility bridge, no legacy command aliases, no placeholder products, and no second interactive command system.

## Decision

`kairospy.surface` is a thin command and presentation layer.

Surface code should:

- declare the Typer command tree
- parse command arguments and options
- map parsed input into application use-case requests
- call application-level facades
- render returned result or view objects

Surface code should not:

- construct infrastructure clients, broker connectors, data stores, or runtime hosts
- import runtime service internals or mode recipes
- maintain a second product registry
- maintain a second parser for the interactive app
- hide business parameters in interactive session state

The dependency direction is:

```text
kairospy.surface
  -> kairospy.application.system
  -> kairospy.application.service.domain

kairospy.application.*
  -> kairospy.infrastructure
  -> kairospy.core
```

`surface` is not the composition root. Application/system code owns operational resources and run lifecycle. Domain application services own user-facing operations that are not run lifecycle concerns, such as reference catalog maintenance, historical market data, account reads, and order workflows.

The stricter target for CLI-facing code is:

```text
kairospy.surface
  -> kairospy.application.system.facade
```

`surface` should prefer system facades as its public application boundary. If a
domain use case needs to be exposed to the CLI, expose it through a small facade
request/result API instead of importing domain services directly from surface.

## Goals

- Make the Typer command tree the single source of command identity.
- Make the interactive app navigate the same Typer command tree as the plain CLI.
- Keep command parameters explicit, especially for trading side effects.
- Keep global command context small and predictable.
- Move infrastructure construction out of surface modules.
- Make output predictable through explicit result/view objects.
- Add tests that catch architecture drift.

## Non-Goals

- Do not preserve old top-level command names.
- Do not keep deprecated aliases.
- Do not keep compatibility modules.
- Do not keep placeholder products for partially migrated modules.
- Do not expose service internals as public CLI APIs.
- Do not make the interactive app execute commands through `CliRunner`.
- Do not introduce a large universal command scope.
- Do not introduce product metadata as a second command registry.

## Typer Command Navigation

Typer owns the command tree.

`surface.cli.app` registers all top-level command groups and user-facing commands. The Typer tree is the only source of truth for:

- command existence
- command path
- help text
- argument and option declarations
- shell validation behavior
- interactive navigation

The interactive app may inspect Typer command metadata through small navigation
helpers, but it must not duplicate the command tree in product specs, context
specs, maturity enums, indexes, or separate registries.

Allowed:

```python
root = root_command(app)
children = child_names(root, ("reference",))
```

Not allowed:

```python
PRODUCTS = {
    "reference": ContextSpec(...),
    "run": ContextSpec(...),
}
```

Presentation metadata can be added later only if it is optional and keyed by command path. It must never decide whether a command exists.

## Command Parameters

Use Typer parameters for command input.

There are two kinds of parameters:

1. Root options
2. Command-specific options and arguments

Root options are truly global process preferences:

```python
@dataclass(frozen=True, slots=True)
class RootOptions:
    cwd: Path | None
    profile: str | None
    output: OutputFormat
    verbose: bool = False
```

Root options may be initialized once from the root callback and passed through `typer.Context.obj`.

Command-specific options stay on the command that owns their semantics:

```bash
kairospy order place --account main --symbol BTC/USDT --side buy --qty 1
kairospy market download --exchange binance --symbol BTC/USDT --timeframe 1m
kairospy reference assets list --type crypto
```

Do not make `account`, `symbol`, `market`, `venue`, `mode`, `start`, or `end` global just because several commands use similar words. Their business meaning depends on the command.

Rules:

- Required business inputs should be explicit command parameters.
- Trading side-effect commands must not rely on hidden session state.
- Root options can provide workspace/profile/output defaults.
- Product commands can resolve their own request objects from `RootOptions` plus their explicit parameters.
- Do not store brokers, provider clients, stores, facades, runtime services, or caches in root context.

Recommended shape:

```python
@app.callback()
def main(
    ctx: typer.Context,
    cwd: Path | None = typer.Option(None, "-C", "--cwd"),
    profile: str | None = typer.Option(None, "--profile"),
    output: OutputFormat = typer.Option(OutputFormat.text, "--output"),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    ctx.obj = RootOptions(
        cwd=cwd,
        profile=profile,
        output=output,
        verbose=verbose,
    )
```

Product handler:

```python
@order_app.command("place")
def place(
    ctx: typer.Context,
    account: str = typer.Option(..., "--account"),
    symbol: str = typer.Option(..., "--symbol"),
    side: OrderSide = typer.Option(..., "--side"),
    qty: Decimal = typer.Option(..., "--qty"),
) -> None:
    root = root_options(ctx)
    request = PlaceOrderRequest(
        workspace=root.workspace,
        profile=root.profile,
        account=account,
        symbol=symbol,
        side=side,
        qty=qty,
    )
    result = order_facade().place(request)
    write_result(result, output=root.output)
```

## Business Request Context

Some values are real business context, but they are not CLI navigation context.

For example, an order placement request commonly needs:

```text
order domain
exchange or venue
broker
account
market
symbol or instrument
side
order type
amount or quantity
price
time in force
```

These values describe the business request: where the order should route, which
account owns it, which market and instrument it targets, and what instruction
should be sent. They should be modeled as explicit request fields, resolved
configuration, or explicit draft objects. They should not be modeled as hidden
interactive session state or command-group path segments.

Recommended shape:

```bash
kairospy order place \
  --account main \
  --venue binance \
  --market spot \
  --symbol BTC/USDT \
  --side buy \
  --type limit \
  --amount 1 \
  --price 65000
```

The command handler maps those explicit inputs into a request object:

```python
request = PlaceOrderRequest(
    workspace=root.workspace,
    profile=root.profile,
    account_id=account,
    venue=venue,
    market=market,
    symbol=symbol,
    side=side,
    order_type=type,
    amount=amount,
    price=price,
)
result = order_facade().place(request)
```

The facade owns request-context resolution:

```text
account id -> account profile
account profile -> broker or exchange credentials
venue + market + symbol -> instrument resolution
order type -> required field validation
amount + price -> precision and tick validation
request -> risk check -> execution adapter
```

This is different from interactive navigation:

```text
CLI navigation context: current command group path
Business request context: account, venue, broker, market, symbol, order type, amount, price
System context: workspace, profile, configuration, credentials, resource construction
UI helper context: selected row, filters, screen mode, draft editing state
```

Interactive helpers may make request context easier to enter, but they must make
the resulting command explicit. Acceptable patterns:

```text
/order> place --account main --venue binance --market spot --symbol BTC/USDT --side buy --type limit --amount 1 --price 65000
```

or an explicit preview before execution:

```text
/order> draft
preview:
kairospy order place --account main --venue binance --market spot --symbol BTC/USDT --side buy --type limit --amount 1 --price 65000
confirm? y
```

or a real draft resource:

```bash
kairospy order draft create --account main --venue binance --market spot
kairospy order draft set <draft-id> --symbol BTC/USDT --side buy --type limit --amount 1 --price 65000
kairospy order draft submit <draft-id>
```

A draft is a business object with an id, inspection, validation, and explicit
submission. It is not hidden shell state.

## Interactive App

The interactive app is a stateful argv composer over the same Typer command tree.

Its navigational state is only the current command group path:

```python
command_group_path = ("reference", "assets")
```

This path is a shell-style working directory for command groups. It is not a
domain context, product state, command default store, or request scope.

Given:

```text
kairos/app/reference/assets> list --type crypto
```

the app executes the same argv as:

```bash
kairospy reference assets list --type crypto
```

Navigation rules:

- If input names a command group, enter that group.
- If input names nested groups, enter the longest matching group path.
- If remaining input exists after the matched group path, execute it under that path.
- Leaf commands do not become context.
- `home`, `back`, `help`, `refresh`, and `quit` are shell commands, not product commands.
- `shell` may remain only as a thin alias to the same app session.
- Hidden `tui`, if present, must use the same app session and Typer navigation helpers.

Allowed interactive state:

```python
@dataclass
class InteractiveSession:
    command_group_path: tuple[str, ...]
    last_output: str | None = None
```

Optional UI-only state is allowed:

- selected table row
- expanded sections
- text filter
- screen mode

UI-only state must not change command semantics.

The command group path may only prefix user input with command group names that
exist in the Typer tree. It must not:

- point at a leaf command
- contain business identifiers such as account, symbol, venue, market, run id, or mode
- provide implicit option defaults
- alter command semantics beyond argv prefix composition

Preferred mental model:

```text
command_group_path + user input = explicit plain CLI argv
```

Not allowed:

```python
session.account = "main"
session.symbol = "BTC/USDT"
```

and then silently executing:

```text
kairos/app/order> place --side buy --qty 1
```

as:

```bash
kairospy order place --account main --symbol BTC/USDT --side buy --qty 1
```

If the app helps reuse a selected value, the command must show that explicitly through completion, a preview, or an explicit flag. It must not silently inject trading parameters.

## Output

Commands should render through explicit output utilities.

Rules:

- Every command supports predictable text output.
- JSON output is stable and built from result/view objects.
- JSONL is reserved for streams or row-oriented output.
- Typer command bodies should not contain large rendering blocks.
- Renderers must not load config, open stores, read catalogs, or construct application services.
- Renderers may write to the requested stream and format already-built data.

Recommended primitive:

```python
class OutputFormat(StrEnum):
    text = "text"
    json = "json"
    jsonl = "jsonl"


def write_result(result: object, *, output: OutputFormat, stdout: TextIO) -> None:
    ...
```

## Application Boundary

Move surface code toward application facades use case by use case.

Do not design a full facade matrix before the commands need it. Add a facade when a surface command currently constructs infrastructure or coordinates business services directly.

Good facade boundary:

```python
request = ListAssetsRequest(type=type, limit=limit)
result = reference_facade(workspace).list_assets(request)
write_result(result, output=root.output)
```

Bad surface boundary:

```python
store = ReferenceStore(root / ".kairos" / "reference")
catalog = ReferenceCatalog(store)
rows = catalog.list_assets(type=type, limit=limit)
```

Rules:

- Surface may import application facades and public request/result types.
- Surface should prefer facade-owned request/result/input types for CLI schemas.
- Surface should not import domain services or core types directly when a facade can expose the required shape.
- Surface must not import infrastructure packages directly.
- Surface must not import runtime hosts, mode recipes, or internal system resources.
- Application facades should return typed result or view objects, not raw service objects.

## Project Module Architecture

The whole project should keep one primary dependency direction:

```text
surface
  -> application.system.facade
  -> application.system internals
  -> application.service.domain
  -> application.runtime
  -> infrastructure
  -> core
```

`core` is the domain model layer. It contains value objects, entities, events,
state transitions, journals, and pure domain rules for account, order,
execution, market, reference, intent, and views. It must not know about Typer,
workspace files, daemons, provider SDKs, databases, or process lifecycle.

`infrastructure` is the adapter layer. It contains stores, data readers/writers,
broker connectors, provider connectors, exchange drivers, and external payload
parsing. It implements ports and concrete persistence/connectivity details, but
it should not decide product workflows or CLI behavior.

`application.service.domain` is the use-case layer for business operations that
can run without owning a process lifecycle. Examples: reference catalog
maintenance, market dataset planning and replay, account snapshots,
reconciliation, and order workflow operations. It coordinates core rules and
ports, but does not render CLI output.

`application.runtime` is the long-running runtime engine. It owns dispatch,
processors, subscriptions, run sessions, pumps, runtime protocol events, and
in-memory runtime state. It should not import `surface` or `application.system`.

`application.service.runtime` and `application.service.modes` are runtime recipe
layers. They define how account, market, reference, and execution services are
wired for backtest, paper, and live modes. They should not own daemon control,
workspace artifacts, or CLI command shape.

`application.system` is the composition and operations layer. It owns workspace
resolution, profiles, resource construction, runtime hosts, daemon control, run
registries, artifacts, logging, and public facades. It is the only application
area allowed to assemble infrastructure adapters into runnable systems for CLI
use.

`application.system.facade` is the public application API for `surface`.
Facades should accept explicit request objects or simple keyword inputs derived
from CLI parameters, resolve workspace/profile concerns, call domain/runtime
services, and return result or view objects. Facades may use internal system
packages, but surface modules must not.

`surface` is the presentation layer. It declares the Typer tree, parses explicit
parameters, stores only `RootOptions`, invokes facades, and renders returned
data. It also owns the interactive argv composer, but that composer only
navigates command groups from the same Typer tree.

`surface` should not be a product framework. Avoid names and packages that imply
product ownership inside surface. In particular, `surface.products` is a
transitional shape; the target name is `surface.cli.commands` because those
modules declare CLI commands, not product domains.

Target surface layout:

```text
kairospy/surface/
  cli/
    __init__.py
    app.py                 Typer root app and command registration
    options.py             RootOptions and root option helpers
    commands/
      project.py           Typer command handlers only
      config.py
      run.py
      account.py
      order.py
      market.py
      reference.py
      strategy.py

  interactive/
    __init__.py
    session.py             InteractiveSession(command_group_path, last_output)
    navigation.py          command-group path matching over Typer metadata
    shell.py               REPL loop over explicit argv composition
    tui.py                 optional UI over the same session and navigation helpers

  rendering/
    __init__.py
    formats.py             OutputFormat
    writer.py              write_result
    text.py
    json.py
    tables.py
```

Naming rules:

- Use `surface.cli.commands`, not `surface.products`, for command handlers.
- Use `surface.cli.app` for the Typer app.
- Use `surface.interactive.shell` for the interactive app session.
- Use `surface.interactive.session` for UI/session state.
- Use `surface.cli.options` for root CLI context.
- Do not keep a broad `surface.state` module.
- Do not place product registries, context specs, maturity metadata, facades, or
  business services in surface.

Recommended module ownership:

```text
kairospy/core/<domain>/...                         pure model and rules
kairospy/infrastructure/<adapter>/...              external systems and stores
kairospy/application/service/domain/<product>/...  product use cases
kairospy/application/runtime/...                   long-running runtime engine
kairospy/application/service/runtime/<area>/...    runtime service adapters
kairospy/application/service/modes/<mode>/...      mode wiring recipes
kairospy/application/system/workspace/...          workspace/profile operations
kairospy/application/system/resources/...          resource construction
kairospy/application/system/host/...               runtime host lifecycle
kairospy/application/system/control/...            daemon and registry control
kairospy/application/system/artifacts/...          run artifacts and logs
kairospy/application/system/facade/<product>.py    public CLI-facing facades
kairospy/surface/cli/app.py                        Typer root and registration
kairospy/surface/cli/commands/<product>.py         command handlers only
kairospy/surface/interactive/navigation.py         Typer navigation utilities
kairospy/surface/interactive/shell.py              interactive argv composer
kairospy/surface/rendering/*.py                    output rendering only
```

`interactive/navigation.py` is not a command model. It must not define
`CommandGroup`, `CommandTree`, `TyperCommandIndex`, product metadata, default
parameters, or business context. Its only job is to provide small functions over
Typer command metadata, such as child names, group existence, token resolution,
and longest group-prefix matching.

Architectural rules:

- A lower layer must not import a higher layer.
- `surface` must not import `infrastructure`, `application.runtime`,
  `application.service.runtime`, `application.service.modes`,
  `application.system.control`, `application.system.host`, or
  `application.system.resources`.
- `surface` should not import `application.service.domain` or `core` directly;
  add or extend a facade instead.
- `application.runtime` must not import `application.system`.
- Mode recipes must not construct system resources or write run artifacts.
- Renderers must not resolve workspaces, build stores, or call services.
- Request/result/view objects are the contract across the surface/application
  boundary.

## Target Command Tree

The final CLI should expose these top-level command groups:

```text
project      workspace creation, status, diagnostics
config       workspace manifest, profiles, effective configuration
run          run specs, run instances, daemon control, logs, artifacts
account      configured accounts, balances, snapshots, account diagnostics
order        order placement, cancellation, replacement, local inspection
market       historical datasets, replay, public market connectivity
reference    assets, instruments, listings, markets, lifecycle, participants
strategy     strategy discovery and validation
app          interactive command workspace
```

There should be no separate `data`, `streams`, `broker`, `backtest`, `paper`, `live`, or `integrations` top-level products.

`kairospy init` should move to `kairospy project init`.

`kairospy run backtest`, `kairospy run paper`, and `kairospy run live` should be removed. `kairospy run start` is the direct run start path; mode comes from the run spec unless daemon operations need an explicit target.

Order commands use explicit account semantics:

```bash
kairospy order open --account <account-id>
kairospy order history --account <account-id>
kairospy order place --account <account-id> --symbol <symbol> --side <side> --qty <qty>
kairospy order cancel --account <account-id> --order-id <order-id>
kairospy order replace --account <account-id> --order-id <order-id> --qty <qty>
kairospy order inspect --account <account-id> --order-id <order-id>
```

Reference nested commands remain valid interactive contexts:

```bash
kairospy reference assets add --symbol <symbol> --type <type>
kairospy reference assets list
kairospy reference assets show <asset-id>
kairospy reference catalog status
kairospy reference catalog search <query>
```

## Deletion List

Delete these concepts in the clean cut:

- `surface.runtime`
- product placeholder loading in `surface.products.__init__`
- `surface.products` as the long-term command package name
- broad `surface.state` modules that mix root options and interactive state
- product maturity enum
- hand-written context registries that duplicate the Typer command tree
- product-specific interactive sessions such as `RunShellSession`
- any `shell` implementation that does not delegate to the shared app session
- any hidden `tui` implementation that does not delegate to the shared app session
- top-level `init`
- `run backtest`
- `run paper`
- `run live`
- any top-level `data`, `streams`, `integrations`, or `backtest` product remnants
- duplicated workspace manifest template in CLI app code and project command code

## Boundary Tests

Add architecture tests that fail on forbidden imports.

Surface must not import:

```text
kairospy.infrastructure
kairospy.application.runtime
kairospy.application.service.runtime
kairospy.application.service.modes
kairospy.application.service.domain
kairospy.application.system.control
kairospy.application.system.host
kairospy.application.system.resources
kairospy.core
```

Allowed surface imports:

```text
kairospy.application.system.facade public facades
```

If a CLI command needs a domain or core concept as input/output shape, expose a
facade-owned request/result/input type instead of importing lower layers from
surface.

Recommended tests:

```python
def test_surface_does_not_import_infrastructure() -> None: ...
def test_surface_does_not_import_runtime_or_mode_services() -> None: ...
def test_surface_does_not_import_system_internals() -> None: ...
def test_cli_and_app_share_typer_navigation() -> None: ...
def test_interactive_context_is_command_group_path_only() -> None: ...
def test_leaf_commands_do_not_become_interactive_context() -> None: ...
def test_interactive_order_does_not_inject_account() -> None: ...
def test_root_options_are_initialized_from_root_callback() -> None: ...
def test_product_commands_do_not_resolve_workspace_directly() -> None: ...
def test_no_placeholder_or_product_specific_shell_remains() -> None: ...
def test_surface_uses_cli_interactive_rendering_packages() -> None: ...
def test_surface_has_no_products_or_broad_state_package() -> None: ...
```

## Implementation Order

This is an implementation order, not a compatibility migration. Each step should leave the command tree coherent.

1. Add tests for Typer navigation helpers and interactive path matching.
2. Split surface into `cli`, `interactive`, and `rendering` packages.
3. Move `surface.products` command modules to `surface.cli.commands`.
4. Make `surface.interactive.shell`, `shell`, and hidden `tui` use the same app session and navigation helpers.
5. Remove product-specific interactive sessions and hand-written context registries.
6. Add `RootOptions` for true root options only.
7. Pass `RootOptions` through `typer.Context.obj`.
8. Keep business parameters explicit on product commands.
9. Add boundary tests for forbidden imports.
10. Move one surface command at a time behind an application facade when it currently constructs infrastructure or coordinates services directly.
11. Delete `surface.runtime` after all surface callers have moved.
12. Remove placeholder products and old top-level commands.
13. Remove low-level run mode commands and keep `run start` as the single direct start path.
14. Re-run the full suite and architecture boundary tests.

## Acceptance Criteria

The refactor is complete when:

- `surface.cli.app` owns the Typer command tree used by both plain CLI and interactive navigation.
- `surface.interactive.shell` does not use `CliRunner`.
- `surface.interactive.shell` does not maintain a second command registry.
- Interactive context is only a Typer command group path.
- Interactive execution preserves the same argv semantics as the plain CLI.
- Interactive order commands do not inject hidden account, symbol, or venue parameters.
- Root command context is limited to true root options.
- Product command handlers keep business parameters explicit.
- Command handlers live under `surface.cli.commands`, not `surface.products`.
- Surface state is split into `surface.cli.options` and `surface.interactive.session`; there is no broad `surface.state` module.
- `surface.runtime` no longer exists.
- Surface modules do not import infrastructure, runtime services, mode services, or internal system resources.
- `kairospy shell` and hidden `kairospy tui`, if present, delegate to the same app session and Typer navigation helpers as `kairospy app`.
- Full tests pass.

## Summary

The mature shape is:

```text
Typer command tree
  -> RootOptions for true global options
  -> CLI command handler with explicit business parameters
  -> application facade/use case
  -> result or view object
  -> surface renderer
```

The interactive app is not a second product system. It is a stateful argv composer over the same Typer command tree:

```text
current command group path + user input = plain CLI argv
```
