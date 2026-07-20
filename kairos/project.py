from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from kairos import __version__


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
            "export MASSIVE_API_KEY=...",
            "kairos doctor",
            "python studies/starter.py",
        ),
    )
    metadata = {**result.to_dict(), "root": ".", "kairos_version": __version__}
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
        Path(".kairos"),
        Path("config"),
        Path("data"),
        Path("data/backtests"),
        Path("data/catalog"),
        Path("data/curated"),
        Path("data/events"),
        Path("data/reference"),
        Path("studies"),
        Path("strategies"),
    )


def _files(project_name: str) -> tuple[tuple[Path, str], ...]:
    return (
        (Path("kairos.toml"), _kairos_toml(project_name)),
        (Path(".env.example"), _env_example()),
        (Path("pyproject.toml"), _pyproject_toml(project_name)),
        (Path(".gitignore"), _gitignore()),
        (Path("README.md"), _readme(project_name)),
        (Path("config/study.json"), _study_config(project_name)),
        (Path("studies/starter.py"), _starter_script()),
        (Path("strategies/__init__.py"), ""),
        (Path("strategies/starter_sma.py"), _starter_strategy()),
        (Path("data/.gitkeep"), ""),
        (Path("data/backtests/.gitkeep"), ""),
        (Path("data/catalog/.gitkeep"), ""),
        (Path("data/curated/.gitkeep"), ""),
        (Path("data/events/.gitkeep"), ""),
        (Path("data/reference/.gitkeep"), ""),
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
    return normalized or "kairos-project"


def _default_project_name(root: Path) -> str:
    if (root / "kairos").is_dir() and (root / "pyproject.toml").exists():
        pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
        if re.search(r'(?m)^name\s*=\s*"(?:kairos|kairospy)"', pyproject):
            return "kairos"
    return root.name or "kairos-project"


def _kairos_toml(project_name: str) -> str:
    return f"""[project]
name = "{project_name}"
timezone = "UTC"

[data]
lake_root = "data"
dataset_root = "data/curated"
catalog_path = "data/catalog/instruments.json"
reference_catalog_path = "data/reference/catalog.json"
event_log_path = "data/events/kairos.jsonl"

[study]
default_study = "starter"

[execution]
default_environment = "simulated"
live_trading_enabled = false

[providers.massive]
api_key = "env:MASSIVE_API_KEY"
timeout_seconds = 30
max_retries = 4

[providers.binance.testnet]
api_key = "env:BINANCE_TESTNET_API_KEY"
api_secret = "env:BINANCE_TESTNET_API_SECRET"

[providers.binance.live]
api_key = "env:BINANCE_LIVE_API_KEY"
api_secret = "env:BINANCE_LIVE_API_SECRET"
"""


def _env_example() -> str:
    return """# kairos.toml references these values with env:VARIABLE_NAME.
# Keep real credentials out of version control.

MASSIVE_API_KEY=

BINANCE_TESTNET_API_KEY=
BINANCE_TESTNET_API_SECRET=

BINANCE_LIVE_API_KEY=
BINANCE_LIVE_API_SECRET=
"""


def _pyproject_toml(project_name: str) -> str:
    return f"""[project]
name = "{project_name}"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["kairospy>=0.1.0"]

[tool.kairos]
config = "kairos.toml"
"""


def _gitignore() -> str:
    return """__pycache__/
*.py[cod]
.pytest_cache/
.venv/
data/backtests/*
data/events/*
data/source/*
!data/**/.gitkeep
"""


def _readme(project_name: str) -> str:
    title = project_name.replace("-", " ").replace("_", " ").title()
    return f"""# {title}

This is a Kairos quantitative study, backtest, and execution project.

## Start

```bash
export MASSIVE_API_KEY=...
kairos doctor
python studies/starter.py
```

Project data lives under `data/`. Keep study code in `studies/` and reusable strategy code in `strategies/`.
Configure providers in `kairos.toml`; credentials should normally be referenced with `env:VARIABLE_NAME`.
"""


def _study_config(project_name: str) -> str:
    return json.dumps({
        "project": project_name,
        "lake_root": "data",
        "default_dataset": "fixture:sma-bars-v1",
        "default_strategy": "sma-cross-v1",
    }, indent=2, sort_keys=True) + "\n"


def _starter_script() -> str:
    return '''from kairos import BacktestRequest, BacktestRunner


def main() -> None:
    request = BacktestRequest(
        strategy="sma-cross-v1",
        dataset="fixture:sma-bars-v1",
        parameters={"fast": 5, "slow": 20},
        artifact_root="data/backtests",
    )
    result = BacktestRunner(lake_root="data").run(request)
    print(result.summary())


if __name__ == "__main__":
    main()
'''


def _starter_strategy() -> str:
    return '''"""Starter strategy parameters for local Kairos experiments."""

STRATEGY_ID = "sma-cross-v1"
PARAMETERS = {
    "fast": 5,
    "slow": 20,
    "fee_bps": 10,
}
'''
