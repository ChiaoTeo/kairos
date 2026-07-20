from __future__ import annotations

from pathlib import Path

from kairospy.ports import Environment
from kairospy.application import ApplicationConfig, RuntimePaths, KairosApplication
from kairospy.application.clock import Clock
from kairospy.orchestration.runtime_store import SQLiteRuntimeStore


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
