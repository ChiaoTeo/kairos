from __future__ import annotations

from pathlib import Path

import kairospy.application.system as system
import kairospy.application.system.trading as trading


ROOT = Path(__file__).resolve().parents[1]


def test_system_facade_exports_launcher_without_trading_internals() -> None:
    assert system.__all__ == ["TradingSystemLauncher"]
    assert "TradingSystem" not in system.__all__
    assert "TradingRuntimeResources" not in system.__all__
    assert "TradingRunSpec" not in system.__all__
    assert trading.__all__ == ["TradingSystemLauncher"]
    assert "TradingSystem" not in trading.__all__
    assert "TradingRuntimeResources" not in trading.__all__
    assert "TradingRunSpec" not in trading.__all__
    assert all(not name.startswith("run_") for name in system.__all__)
    assert all(not name.startswith("run_") for name in trading.__all__)


def test_service_modes_do_not_import_system_trading_startup() -> None:
    service_modes = ROOT / "kairospy" / "application" / "service" / "modes"
    forbidden = (
        "application.system.trading",
        "TradingSystem",
        "TradingRuntimeResources",
        "TradingRunSpec",
    )
    offenders = []
    for path in service_modes.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert offenders == []


def test_surface_uses_system_facade_for_run_startup() -> None:
    surface_files = (
        ROOT / "kairospy" / "surface" / "products" / "run.py",
        ROOT / "kairospy" / "surface" / "products" / "backtest.py",
    )
    forbidden = (
        "RuntimeKernel",
        "RuntimeRunSpec",
        "TradingSystem(",
        "TradingRuntimeResources",
        "TradingRunSpec",
        "application.system.trading",
    )
    offenders = []
    for path in surface_files:
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert offenders == []
