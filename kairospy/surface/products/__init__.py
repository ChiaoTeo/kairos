from __future__ import annotations

import importlib

import typer


def _product_app(module_name: str, app_name: str, label: str) -> typer.Typer:
    try:
        module = importlib.import_module(f"{__name__}.{module_name}")
    except ModuleNotFoundError as error:
        if not str(error).startswith("No module named 'kairospy.application."):
            raise
        return _placeholder_app(label, error)
    return getattr(module, app_name)


def _placeholder_app(label: str, error: ModuleNotFoundError) -> typer.Typer:
    app = typer.Typer(no_args_is_help=True, help=f"{label} commands")

    @app.callback(invoke_without_command=True)
    def _unavailable(ctx: typer.Context) -> None:
        if ctx.invoked_subcommand is None:
            typer.echo(f"{label} commands have not been migrated to the rewritten runtime yet: {error}")
            raise typer.Exit(2)

    return app


backtest_app = _product_app("backtest", "backtest_app", "Backtest")
broker_app = _product_app("broker", "broker_app", "Broker")
data_app = _product_app("data", "data_app", "Data")
integrations_app = _product_app("integrations", "integrations_app", "Integrations")
reference_app = _product_app("reference", "reference_app", "Reference")
run_app = _product_app("run", "run_app", "Run")
strategy_app = _product_app("strategy", "strategy_app", "Strategy")
streams_app = _product_app("streams", "streams_app", "Streams")


__all__ = [
    "backtest_app",
    "broker_app",
    "data_app",
    "integrations_app",
    "reference_app",
    "run_app",
    "strategy_app",
    "streams_app",
]
