"""Launch-scoped explicit CLI command tree."""

from __future__ import annotations

import typer


launch_app = typer.Typer(no_args_is_help=True, help="Manage launch instances")
draft_app = typer.Typer(no_args_is_help=True, help="Manage persisted Launch drafts")
launch_app.add_typer(draft_app, name="draft")
strategy_app = typer.Typer(
    no_args_is_help=True, help="Manage the strategy inside a launch instance"
)
launch_app.add_typer(strategy_app, name="strategy")
instance_app = typer.Typer(no_args_is_help=True, help="Inspect a launch instance")
instance_component_app = typer.Typer(
    no_args_is_help=True, help="Connect to components bound to a launch instance"
)
instance_component_market_app = typer.Typer(
    no_args_is_help=True,
    help="Connect to the Market component bound to a launch instance",
)
instance_component_account_app = typer.Typer(
    no_args_is_help=True,
    help="Connect to Account components bound to a launch instance",
)
instance_component_execution_app = typer.Typer(
    no_args_is_help=True,
    help="Connect to the Execution component bound to a launch instance",
)
instance_component_reference_app = typer.Typer(
    no_args_is_help=True,
    help="Connect to the Reference component bound to a launch instance",
)
instance_component_risk_app = typer.Typer(
    no_args_is_help=True,
    help="Connect to the Risk component bound to a launch instance",
)
instance_component_capital_app = typer.Typer(
    no_args_is_help=True,
    help="Connect to the Capital component bound to a launch instance",
)
instance_timeline_app = typer.Typer(
    no_args_is_help=True, help="Inspect lifecycle records from one launch instance"
)
launch_app.add_typer(instance_app, name="instance")
instance_app.add_typer(instance_component_app, name="component")
instance_component_app.add_typer(instance_component_account_app, name="account")
instance_component_app.add_typer(instance_component_market_app, name="market")
instance_component_app.add_typer(instance_component_execution_app, name="execution")
instance_component_app.add_typer(instance_component_reference_app, name="reference")
instance_component_app.add_typer(instance_component_risk_app, name="risk")
instance_component_app.add_typer(instance_component_capital_app, name="capital")
instance_app.add_typer(instance_timeline_app, name="timeline")


def _group(name: str, commands: tuple[str, ...]) -> typer.Typer:
    descriptions = {
        "targets": "Manage reusable launch targets.",
        "diagnose": "Validate and explain launch configuration.",
        "replay": "Inspect replay input and progress.",
    }
    group = typer.Typer(
        no_args_is_help=True, help=descriptions.get(name, f"Launch {name} commands")
    )
    launch_app.add_typer(group, name=name)
    del commands
    return group


targets_app = _group("targets", ("add", "remove", "index", "list", "browse"))
diagnose_app = _group("diagnose", ("validate", "explain"))
replay_app = _group("replay", ("events",))


from . import account as _account  # noqa: E402,F401
from . import capital as _capital  # noqa: E402,F401
from . import execution as _execution  # noqa: E402,F401
from . import market as _market  # noqa: E402,F401
from . import reference as _reference  # noqa: E402,F401
from . import risk as _risk  # noqa: E402,F401
from . import lifecycle as _lifecycle  # noqa: E402,F401
from . import operations as _operations  # noqa: E402,F401
from . import setup as _setup  # noqa: E402,F401
from . import strategy as _strategy  # noqa: E402,F401
from . import timeline as _timeline  # noqa: E402,F401
