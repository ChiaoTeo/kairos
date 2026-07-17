from __future__ import annotations

from pathlib import Path

from trading.adapters.base import Environment
from trading.application import ApplicationConfig, RuntimePaths, TradingApplication
from trading.application.clock import Clock
from trading.orchestration.runtime_store import SQLiteRuntimeStore


def operational_application(
    root: str | Path,
    store: SQLiteRuntimeStore,
    *,
    clock: Clock | None = None,
    environment: Environment = Environment.TESTNET,
) -> TradingApplication:
    base = Path(root)
    paths = RuntimePaths(base, base / "reference" / "catalog.json", base, store.path, base / "artifacts")
    application = TradingApplication(
        ApplicationConfig(environment, paths), store,
        runtime_id=f"test-{base.name}-{id(store)}", clock=clock,
    )
    application.start()
    application.run()
    return application
