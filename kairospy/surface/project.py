from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from kairospy import __version__
from kairospy.infrastructure.configuration import DEFAULT_LAKE_ROOT, PROJECT_STATE_DIR


@dataclass(frozen=True, slots=True)
class ProjectInitResult:
    root: Path
    name: str
    created: tuple[str, ...]
    reused: tuple[str, ...]
    next_steps: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "name": self.name,
            "created": list(self.created),
            "reused": list(self.reused),
            "next_steps": list(self.next_steps),
        }


def initialize_project(target: str | Path = ".", *, name: str | None = None, force: bool = False) -> ProjectInitResult:
    root = Path(target).expanduser().resolve()
    if root.exists() and not root.is_dir():
        raise ValueError(f"Kairos project target is not a directory: {root}")
    root.mkdir(parents=True, exist_ok=True)
    project_name = _project_name(name or _default_project_name(root))
    created: list[str] = []
    reused: list[str] = []

    for directory in _directories():
        _ensure_directory(root / directory, created, reused, root)

    for relative, content in _files(project_name):
        _write_file(root / relative, content, force=force, created=created, reused=reused, root=root)

    result = ProjectInitResult(
        root=root,
        name=project_name,
        created=tuple(created),
        reused=tuple(reused),
        next_steps=(
            "kairospy config validate",
            "kairospy data start",
            "kairospy workspace create alpha",
            "kairospy run config validate configs/runs/backtest.example.toml",
        ),
    )
    metadata = {**result.to_dict(), "root": ".", "kairospy_version": __version__}
    _write_file(
        root / ".kairos" / "project.json",
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        force=True,
        created=created,
        reused=reused,
        root=root,
    )
    return ProjectInitResult(root, project_name, tuple(created), tuple(reused), result.next_steps)


def render_project_init(result: ProjectInitResult) -> str:
    lines = [
        "Kairos project initialized",
        "",
        f"Root: {result.root}",
        f"Name: {result.name}",
    ]
    if result.created:
        lines.extend(["", "Created:"])
        lines.extend(f"  {item}" for item in result.created)
    if result.reused:
        lines.extend(["", "Reused:"])
        lines.extend(f"  {item}" for item in result.reused)
    lines.extend(["", "Next:"])
    lines.extend(f"  {item}" for item in result.next_steps)
    return "\n".join(lines)


def _directories() -> tuple[Path, ...]:
    return (
        Path(PROJECT_STATE_DIR),
        Path(DEFAULT_LAKE_ROOT),
        Path(DEFAULT_LAKE_ROOT) / "backtests",
        Path(DEFAULT_LAKE_ROOT) / "catalog",
        Path(DEFAULT_LAKE_ROOT) / "curated",
        Path(DEFAULT_LAKE_ROOT) / "events",
        Path(DEFAULT_LAKE_ROOT) / "reference",
        Path(PROJECT_STATE_DIR) / "workspace",
        Path(PROJECT_STATE_DIR) / "run",
        Path(PROJECT_STATE_DIR) / "governance",
        Path("configs") / "runs",
    )


def _files(project_name: str) -> tuple[tuple[Path, str], ...]:
    return (
        (Path("kairos.toml"), _kairospy_toml(project_name)),
        (Path(".env.example"), _env_example()),
        (Path("workspace") / "example.py", _workspace_example_py()),
        (Path("configs") / "runs" / "backtest.example.toml", _run_config_backtest_example()),
        (Path("configs") / "runs" / "paper.example.toml", _run_config_paper_example()),
        (Path("configs") / "runs" / "live.example.toml", _run_config_live_example()),
        (Path("pyproject.toml"), _pyproject_toml(project_name)),
        (Path(".gitignore"), _gitignore()),
        (Path("README.md"), _readme(project_name)),
    )


def _write_file(path: Path, content: str, *, force: bool, created: list[str], reused: list[str], root: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    relative = _relative(path, root)
    if path.exists() and not force:
        reused.append(relative)
        return
    path.write_text(content, encoding="utf-8")
    created.append(relative)


def _ensure_directory(path: Path, created: list[str], reused: list[str], root: Path) -> None:
    relative = _relative(path, root) + "/"
    if path.exists():
        reused.append(relative)
        return
    path.mkdir(parents=True, exist_ok=True)
    created.append(relative)


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _project_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-._").lower()
    return normalized or "kairospy-project"


def _default_project_name(root: Path) -> str:
    if (root / "kairospy").is_dir() and (root / "pyproject.toml").exists():
        pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
        if re.search(r'(?m)^name\s*=\s*"(?:kairospy|kairospy)"', pyproject):
            return "kairospy"
    return root.name or "kairospy-project"


def _kairospy_toml(project_name: str) -> str:
    return f"""# Kairos project configuration.
# Secrets should normally stay in environment variables and be referenced as env:NAME.

schema_version = 1

[project]
name = "{project_name}"
timezone = "UTC"

[paths]
# All relative paths are resolved from this project directory.
lake_root = "{DEFAULT_LAKE_ROOT}"
workspace_root = ".kairos/workspace"
run_root = ".kairos/run"
governance_root = ".kairos/governance"
reference_catalog = ".kairos/data/reference/catalog.json"

[data]
default_quality = "Q2"
default_provider = "auto"

[execution]
default_environment = "simulated"
live_trading_enabled = false
paper_account_id = "kairospy-paper"
shadow_account_id = "kairospy-shadow"

[cli]
format = "text"
language = "auto"
run_control = true

[credentials.massive_marketdata_primary]
purpose = "market_data"
kind = "api_key"
api_key = "env:KAIROS_MASSIVE_MARKETDATA_PRIMARY_API_KEY"

[credentials.binance_trading_testnet_spot]
purpose = "trading"
kind = "api_key_secret"
api_key = "env:KAIROS_BINANCE_TRADING_TESTNET_SPOT_API_KEY"
api_secret = "env:KAIROS_BINANCE_TRADING_TESTNET_SPOT_API_SECRET"

[credentials.binance_trading_live_spot]
purpose = "trading"
kind = "api_key_secret"
api_key = "env:KAIROS_BINANCE_TRADING_LIVE_SPOT_API_KEY"
api_secret = "env:KAIROS_BINANCE_TRADING_LIVE_SPOT_API_SECRET"

[credentials.hyperliquid_trading_live_perp]
purpose = "trading"
kind = "private_key"
private_key = "env:KAIROS_HYPERLIQUID_LIVE_PRIVATE_KEY"
account_address = "env:KAIROS_HYPERLIQUID_LIVE_ACCOUNT_ADDRESS"

[providers.massive]
type = "data_vendor"
enabled = true

[providers.massive.services.historical_market_data]
credential = "massive_marketdata_primary"
timeout_seconds = 30
max_retries = 4

[providers.binance]
type = "exchange"
enabled = true

[providers.binance.services.market_data]
environment = "live"
credential = ""
public = true

[providers.binance.services.execution_testnet]
environment = "testnet"
credential = "binance_trading_testnet_spot"

[providers.binance.services.execution_live]
environment = "live"
credential = "binance_trading_live_spot"

[providers.hyperliquid]
type = "exchange"
enabled = true

[providers.hyperliquid.services.market_data]
environment = "live"
credential = ""
public = true

[providers.hyperliquid.services.execution_live]
environment = "live"
credential = "hyperliquid_trading_live_perp"

[accounts.binance_testnet_spot]
account_ref = "binance:crypto_spot:testnet"
provider = "binance"
environment = "testnet"
credential = "binance_trading_testnet_spot"
permissions = ["account:read", "order:write", "order:cancel"]
allowed_products = ["spot"]
capital_scope = "testnet"

[accounts.binance_live_spot]
account_ref = "binance:crypto_spot:main"
provider = "binance"
environment = "live"
credential = "binance_trading_live_spot"
permissions = ["account:read", "order:write", "order:cancel"]
allowed_products = ["spot"]
capital_scope = "main-live"

[accounts.hyperliquid_live_perp]
account_ref = "hyperliquid:derivatives:main"
provider = "hyperliquid"
environment = "live"
credential = "hyperliquid_trading_live_perp"
permissions = ["account:read", "order:write", "order:cancel"]
allowed_products = ["perpetual"]
capital_scope = "main-live"
"""


def _env_example() -> str:
    return f"""# kairos.toml references these values with env:VARIABLE_NAME.
# Keep real credentials out of version control.

KAIROS_MASSIVE_MARKETDATA_PRIMARY_API_KEY=

KAIROS_BINANCE_TRADING_TESTNET_SPOT_API_KEY=
KAIROS_BINANCE_TRADING_TESTNET_SPOT_API_SECRET=

KAIROS_BINANCE_TRADING_LIVE_SPOT_API_KEY=
KAIROS_BINANCE_TRADING_LIVE_SPOT_API_SECRET=

KAIROS_HYPERLIQUID_LIVE_PRIVATE_KEY=
KAIROS_HYPERLIQUID_LIVE_ACCOUNT_ADDRESS=

# Optional runtime overrides.
KAIROSPY_LAKE_ROOT={DEFAULT_LAKE_ROOT}
"""


def _run_config_backtest_example() -> str:
    return """schema_version = 1

[run]
name = "example-backtest"
mode = "backtest"
workspace = "workspace.example:build_workspace"
strategy = "kairospy.workspace.defaults:EmptyStrategy"

[params]
workspace_profile = "alpha"
fast = 20
slow = 50

[backtest]
start = "2025-01-01T00:00:00+00:00"
end = "2026-01-01T00:00:00+00:00"
initial_cash = "1000000"
base_currency = "USD"

[guards]
freeze_workspace = true
fail_on_missing_data = true
"""


def _run_config_paper_example() -> str:
    return """schema_version = 1

[run]
name = "example-paper"
mode = "paper"
workspace = "workspace.example:build_workspace"
strategy = "kairospy.workspace.defaults:EmptyStrategy"

[params]
workspace_profile = "alpha"
risk_budget = "0.02"

[bindings]
account = "binance_testnet_spot"
market = ["ticks"]

[paper]
environment = "testnet"
execution_driver = "binance-testnet"
recovery_policy = "recover-and-reconcile"

[guards]
require_live_view_freshness = true
require_account_query = true
require_reconciliation = true
"""


def _run_config_live_example() -> str:
    return """schema_version = 1

[run]
name = "example-live"
mode = "live"
workspace = "workspace.example:build_workspace"
strategy = "kairospy.workspace.defaults:EmptyStrategy"

[params]
workspace_profile = "alpha"
max_gross_exposure = "50000"
max_order_notional = "1000"

[bindings]
account = "binance_live_spot"
market = ["ticks"]
execution = "binance_live_spot"

[live]
provider = "binance"
execution_driver = "binance-live"
recovery_policy = "recover-and-reconcile"

[guards]
confirm_live_required = true
require_readiness = true
require_promotion = true
require_account_lock = true
require_order_recovery = true
require_reconciliation = true
start_reduce_only = true

[evidence]
readiness = "governance:readiness/example-live.json"
promotion = "governance:promotion/example-live.json"
"""


def _pyproject_toml(project_name: str) -> str:
    return f"""[project]
name = "{project_name}"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["kairospy>=0.1.0"]

[tool.kairospy]
config = "kairos.toml"
"""


def _workspace_example_py() -> str:
    return '''from __future__ import annotations


def build_workspace(ws, params):
    profile = str(params.get("workspace_profile") or "alpha")
    attachments = ws.attachments.use_profile(profile)
    market = attachments.as_ohlcv("market", required=False)
    momentum = ws.features.momentum(name="momentum", source=market, window=int(params.get("fast", "20")))
    volatility = ws.features.realized_volatility(name="realized_volatility", source=market, window=int(params.get("slow", "50")))
    return ws.project(
        market=(market,),
        features=(momentum, volatility),
        portfolio={"cash": "simulated"},
    )
'''


def _gitignore() -> str:
    return """__pycache__/
*.py[cod]
.pytest_cache/
.venv/
.env
.kairos/*
!.kairos/project.json
"""


def _readme(project_name: str) -> str:
    title = project_name.replace("-", " ").replace("_", " ").title()
    return f"""# {title}

This is a Kairos quantitative data, strategy code, and run project.

## Start

```bash
export KAIROS_MASSIVE_MARKETDATA_PRIMARY_API_KEY=...
kairospy config validate
kairospy data start
kairospy workspace create alpha
kairospy workspace attach alpha --name market --dataset your.dataset --view both
kairospy run config validate configs/runs/backtest.example.toml
```

Kairos-managed data lives under `.kairos/data/`. Workspace bindings live under `.kairos/workspace/`,
and run artifacts live under `.kairos/run/`. Keep your Python code in whichever source directory
fits your project.
Reusable run configuration lives under `configs/runs/`. Configure providers only in `kairos.toml`;
account bindings also live there.
credentials should normally be referenced with `env:VARIABLE_NAME`.
"""
