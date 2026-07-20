from __future__ import annotations

from pathlib import Path

from kairos.ports import Environment
from kairos.application import ApplicationConfig, RuntimePaths, KairosApplication
from kairos.application.clock import Clock
from kairos.orchestration.runtime_store import SQLiteRuntimeStore


def operational_application(
    root: str | Path,
    store: SQLiteRuntimeStore,
    *,
    clock: Clock | None = None,
    environment: Environment = Environment.TESTNET,
) -> KairosApplication:
    base = Path(root)
    paths = RuntimePaths(base, base / "reference" / "catalog.json", base, store.path, base / "artifacts")
    application = KairosApplication(
        ApplicationConfig(environment, paths), store,
        runtime_id=f"test-{base.name}-{id(store)}", clock=clock,
    )
    application.start()
    application.run()
    return application
