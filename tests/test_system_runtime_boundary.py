from __future__ import annotations

from pathlib import Path

from kairospy.application.system.application.resources import TradingLaunchSpec, TradingSystemResources
from kairospy.application.system.services.system import TradingSystem
from kairospy.application.support.launch.application.resources import LaunchAssembly
from kairospy.application.support.launch.domain.modes import RuntimeMode
from kairospy.application.support.runtime.application.interaction import SystemCallResult


ROOT = Path(__file__).parents[1]


def test_system_session_does_not_reach_into_runtime_internals() -> None:
    source = (ROOT / "kairospy/application/system/services/system.py").read_text()

    assert ".session.session" not in source
    assert ".kernel" not in source
    assert ".frame" not in source
    assert "connection_scope.start" not in source
    assert "connection_scope.stop" not in source
    assert "connections.stop" not in source


def test_reference_is_ready_before_strategy_on_start_without_entering_biz_components() -> None:
    order: list[str] = []

    class Reference:
        def ensure_ready(self) -> None:
            order.append("reference.ready")

    reference = Reference()

    class Strategy:
        strategy_id = "reference-preflight"

        def on_start(self, context) -> None:
            assert context.reference is reference
            order.append("strategy.start")

        def on_data(self, context, signal) -> None:
            return None

        def on_intent(self, context, intent) -> None:
            return None

        def on_clock(self, context, signal) -> None:
            return None

        def on_system(self, context, signal) -> None:
            return None

        def on_end(self, context) -> None:
            return None

    class Business:
        system_call = object()
        strategy_reference = reference
        def start(self, **kwargs):
            kwargs["resources"].reference.ensure_ready()
            return self

        def attach(self, **kwargs) -> None:
            return None

        def bind_runtime(self, runtime) -> None:
            return None

        @property
        def intents(self):
            return object()

        def bind_connections(self, connections) -> None:
            return None

        def unbind_connections(self) -> None:
            return None

        def process(self, event):
            return ()

        def call(self, command) -> SystemCallResult:
            raise AssertionError("test does not issue system calls")

        def detach(self) -> None:
            return None

    spec = TradingLaunchSpec(
        launch_id="reference-preflight",
        mode=RuntimeMode.SYSTEM,
        strategy=Strategy(),
        resources=TradingSystemResources(
            reference=reference,
            assembly=LaunchAssembly(output=lambda *args, **kwargs: object()),
            business=Business(),
        ),
        launch_directory=Path("."),
        normalized_config={},
    )

    system = TradingSystem(spec)
    session = system.start()
    try:
        assert order == ["reference.ready", "strategy.start"]
    finally:
        session.stop()
