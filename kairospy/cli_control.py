from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class RunControlState:
    strategy: str
    mode: str
    status: str
    started_at: str
    pipeline: tuple[tuple[str, str, str], ...]
    metrics: tuple[tuple[str, object], ...]
    events: tuple[str, ...] = ()


def initial_run_control_state(strategy: str, mode: str) -> RunControlState:
    return RunControlState(
        strategy=strategy,
        mode=mode,
        status="RUNNING",
        started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        pipeline=(
            ("Data", "PENDING", "loading input"),
            ("Factor", "PENDING", "waiting for data"),
            ("Strategy", "PENDING", "not started"),
            ("Execution", "IDLE", "no orders submitted"),
            ("Artifact", "PENDING", "not written"),
        ),
        metrics=(("Bars", "-"), ("Orders", "-"), ("Fills", "-"), ("Final Equity", "-")),
        events=("run accepted",),
    )


def completed_run_control_state(strategy: str, payload: Mapping[str, object]) -> RunControlState:
    mode = str(payload.get("mode", "run"))
    orders = payload.get("orders", payload.get("trades", 0))
    fills = payload.get("fills", payload.get("trades", 0))
    final_equity = payload.get("final_equity") or payload.get("final_cash") or "-"
    return RunControlState(
        strategy=strategy,
        mode=mode,
        status="PASSED" if payload.get("passed", True) else "FAILED",
        started_at="-",
        pipeline=(
            ("Data", "OK", str(payload.get("input_identity", "input loaded"))),
            ("Factor", "OK", _short_hash(payload.get("factor_hash"))),
            ("Strategy", "OK", _short_hash(payload.get("decision_hash"))),
            ("Execution", "OK", f"orders={orders} fills={fills}"),
            ("Artifact", "OK", str(payload.get("artifact", "-"))),
        ),
        metrics=(
            ("Bars", payload.get("bars", "-")),
            ("Trades", payload.get("trades", "-")),
            ("Orders", orders),
            ("Fills", fills),
            ("Final Equity", final_equity),
            ("Audit Hash", _short_hash(payload.get("audit_hash"))),
        ),
        events=tuple(_events_from_payload(payload)),
    )


def render_run_control(state: RunControlState) -> str:
    rendered = _render_rich_run_control(state)
    if rendered is not None:
        return rendered
    sections = [
        "Kairos Run Control",
        "",
        f"Strategy  {state.strategy}",
        f"Mode      {state.mode}",
        f"Status    {state.status}",
        f"Started   {state.started_at}",
        "",
        "Pipeline",
        *[f"  {name:<10} {status:<8} {detail}" for name, status, detail in state.pipeline],
        "",
        "Metrics",
        *[f"  {name:<12} {value}" for name, value in state.metrics],
    ]
    if state.events:
        sections.extend(["", "Recent Events", *[f"  {event}" for event in state.events[-5:]]])
    return "\n".join(sections).rstrip()


def render_run_summary(strategy: str, payload: Mapping[str, object]) -> str:
    state = completed_run_control_state(strategy, payload)
    rendered = _render_rich_run_summary(state, payload)
    if rendered is not None:
        return rendered
    return render_run_control(state)


def _render_rich_run_control(state: RunControlState) -> str | None:
    try:
        from rich.console import Console
        from rich.layout import Layout
        from rich.panel import Panel
        from rich.table import Table
    except Exception:
        return None
    console = Console(force_terminal=False, color_system=None, width=120)
    layout = Layout(name="root")
    layout.split_column(Layout(name="header", size=6), Layout(name="body"), Layout(name="events", size=7))
    layout["body"].split_row(Layout(name="pipeline"), Layout(name="metrics"))
    layout["header"].update(Panel(
        f"Strategy: {state.strategy}\nMode: {state.mode}\nStatus: {state.status}\nStarted: {state.started_at}",
        title="Kairos Run Control",
    ))
    pipeline = Table(show_header=True)
    pipeline.add_column("Stage")
    pipeline.add_column("Status")
    pipeline.add_column("Detail")
    for name, status, detail in state.pipeline:
        pipeline.add_row(name, status, detail)
    metrics = Table(show_header=False)
    metrics.add_column("Metric")
    metrics.add_column("Value")
    for name, value in state.metrics:
        metrics.add_row(name, str(value))
    layout["pipeline"].update(Panel(pipeline, title="Pipeline"))
    layout["metrics"].update(Panel(metrics, title="Metrics"))
    layout["events"].update(Panel("\n".join(state.events[-5:]) or "-", title="Recent Events"))
    with console.capture() as capture:
        console.print(layout)
    return capture.get().rstrip()


def _render_rich_run_summary(state: RunControlState, payload: Mapping[str, object]) -> str | None:
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table
    except Exception:
        return None
    console = Console(force_terminal=False, color_system=None, width=120)
    table = Table(title="Kairos Run Summary", show_header=False)
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Strategy", state.strategy)
    table.add_row("Mode", state.mode)
    table.add_row("Status", state.status)
    table.add_row("Artifact", str(payload.get("artifact", "-")))
    table.add_row("Audit Hash", str(payload.get("audit_hash", "-")))
    metrics = "\n".join(f"{name}: {value}" for name, value in state.metrics)
    next_steps = "\n".join(f"{index}. {step}" for index, step in enumerate(_next_steps(payload), start=1))
    with console.capture() as capture:
        console.print(table)
        console.print(Panel(metrics, title="Metrics"))
        if next_steps:
            console.print(Panel(next_steps, title="Next Steps"))
    return capture.get().rstrip()


def _events_from_payload(payload: Mapping[str, object]) -> Sequence[str]:
    events = ["data loaded", "factor evaluated", "strategy decisions completed"]
    if payload.get("orders") or payload.get("trades"):
        events.append("execution simulated")
    if payload.get("artifact"):
        events.append("artifact written")
    return events


def _next_steps(payload: Mapping[str, object]) -> tuple[str, ...]:
    artifact = payload.get("artifact")
    capture = payload.get("capture")
    if not artifact:
        return ()
    steps = [f"kairospy run inspect --artifact {artifact}", f"kairospy run artifact-replay --artifact {artifact} --fixture"]
    if capture:
        steps.append(f"kairospy run capture-replay --artifact {artifact} --capture {capture}")
    return tuple(steps)


def _short_hash(value: object) -> str:
    if not value:
        return "-"
    text = str(value)
    return text if len(text) <= 16 else text[:12] + "..."
