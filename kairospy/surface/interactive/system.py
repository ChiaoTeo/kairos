from __future__ import annotations

import json
import shlex
from dataclasses import fields, is_dataclass
from decimal import Decimal
from datetime import datetime, timezone
from typing import Mapping

from prettytable import PrettyTable

from kairospy.application.modes import RuntimeMode
from kairospy.application.strategy import cli_command_envelope
from kairospy.application.launch.facade import DEFAULT_SYSTEM_LAUNCH_ID, LaunchFacade


class InteractiveSystemSession:
    def __init__(
        self,
        *,
        strategy_path: str = "kairospy.application.strategy:CliStrategyBase",
        launch_id: str = DEFAULT_SYSTEM_LAUNCH_ID,
        mode: RuntimeMode = RuntimeMode.SYSTEM,
        launches: LaunchFacade | None = None,
    ) -> None:
        self.strategy_path = strategy_path
        self.launch_id = launch_id
        self.mode = mode
        self._launches = launches or LaunchFacade()
        self._runtime = self._launches.open_system_session(strategy_path=strategy_path, launch_id=launch_id, mode=mode)
        self._sequence = 1
        self._closed = False

    def handle(self, line: str) -> str:
        command = parse_system_command(line)
        self._runtime.process(
            cli_command_envelope(
                str(command["command"]),
                command["args"],  # type: ignore[arg-type]
                time=datetime.now(timezone.utc),
                sequence=self._sequence,
            )
        )
        self._sequence += 1
        return render_command_result(command, self._runtime)

    def finish(self) -> object:
        result = self._runtime.finish()
        self._runtime.complete()
        return result

    def close(self) -> None:
        if self._closed:
            return
        self._runtime.close()
        self._closed = True


def parse_system_command(line: str) -> dict[str, object]:
    parts = shlex.split(line.strip())
    if not parts:
        raise ValueError("empty system command")
    if parts[0] == "command":
        if len(parts) < 2:
            raise ValueError("command requires a CliStrategy command name")
        return {"command": parts[1], "args": _json_object(parts[2]) if len(parts) > 2 else {}}
    if parts[0] == "trace":
        values = _parse_options(parts[1:])
        name = values.pop("name", values.pop("_0", "cli"))
        payload = values.pop("payload", values.pop("_1", "{}"))
        _reject_extra(values)
        return {"command": "trace", "args": {"name": name, "payload": _json_object(str(payload))}}
    if parts[:2] == ["account", "current"]:
        values = _parse_options(parts[2:])
        account = values.pop("account", values.pop("_0", None))
        _reject_extra(values)
        return {"command": "account.current", "args": _without_none({"account": account})}
    if parts[:2] == ["account", "balance"]:
        values = _parse_options(parts[2:])
        currency = values.pop("currency", values.pop("_0", None))
        if currency is None:
            raise ValueError("account balance requires currency")
        account = values.pop("account", values.pop("_1", None))
        _reject_extra(values)
        return {"command": "account.balance", "args": _without_none({"account": account, "currency": currency})}
    if parts[:2] == ["account", "position"]:
        values = _parse_options(parts[2:])
        instrument = values.pop("instrument", values.pop("_0", None))
        if instrument is None:
            raise ValueError("account position requires instrument")
        account = values.pop("account", values.pop("_1", None))
        _reject_extra(values)
        return {"command": "account.position", "args": _without_none({"account": account, "instrument": instrument})}
    if parts[:2] == ["order", "target-position"]:
        values = _parse_options(parts[2:])
        instrument = values.pop("instrument", values.pop("_0", None))
        quantity = values.pop("quantity", values.pop("_1", None))
        if instrument is None or quantity is None:
            raise ValueError("order target-position requires instrument and quantity")
        args = {
            "account": values.pop("account", None),
            "book": values.pop("book", None),
            "instrument": instrument,
            "intent_id": values.pop("intent-id", values.pop("intent_id", None)),
            "limit_price": values.pop("limit-price", values.pop("limit_price", None)),
            "quantity": quantity,
            "reason": values.pop("reason", None),
        }
        _reject_extra(values)
        return {"command": "target_position", "args": _without_none(args)}
    raise ValueError(f"unsupported system command: {parts[0]}")


def parse_system_entry(parts: list[str]) -> dict[str, object]:
    values = _parse_options(parts)
    if "strategy" in values or "_0" in values:
        raise ValueError("system mode uses the built-in CliStrategyBase")
    if "mode" in values:
        raise ValueError("system mode does not accept --mode")
    launch_id = values.pop("launch-id", values.pop("launch_id", DEFAULT_SYSTEM_LAUNCH_ID))
    _reject_extra(values)
    return {"launch_id": str(launch_id)}


def render_system_result(result: object) -> str:
    runtime = getattr(result, "runtime", None)
    mode = getattr(getattr(result, "mode", None), "value", getattr(result, "mode", None))
    return "\n".join(
        [
            f"launch_id: {getattr(result, 'launch_id', None)}",
            f"mode: {mode}",
            f"strategy: {getattr(runtime, 'strategy_id', None)}",
            f"events: {getattr(runtime, 'event_count', None)}",
            f"intents: {getattr(runtime, 'intent_count', None)}",
        ]
    )


def render_command_result(command: Mapping[str, object], runtime: object) -> str:
    name = str(command.get("command") or "")
    if name.startswith("account."):
        return _render_account_command(name, runtime)
    if name == "target_position":
        return _render_latest_intent(runtime)
    if name == "trace":
        trace = _latest_trace(runtime)
        payload = {} if trace is None else _mapping(getattr(trace, "payload", None))
        label = "trace" if trace is None else getattr(trace, "name", "trace")
        return f"{label}: {_compact(payload)}"
    return f"ok {name}"


def system_help_text() -> str:
    return "\n".join(
        [
            "account current [ACCOUNT|--account ACCOUNT]",
            "account balance CURRENCY [--account ACCOUNT]",
            "account position INSTRUMENT [--account ACCOUNT]",
            "order target-position INSTRUMENT QUANTITY [--account ACCOUNT] [--book BOOK] [--limit-price PRICE]",
            "trace NAME '{\"key\":\"value\"}'",
            "command CLI_COMMAND '{\"arg\":\"value\"}'",
            "exit-system",
        ]
    )


def _parse_options(parts: list[str]) -> dict[str, object]:
    values: dict[str, object] = {}
    position = 0
    index = 0
    while index < len(parts):
        item = parts[index]
        if item.startswith("--"):
            key = item[2:]
            if not key:
                raise ValueError("empty option name")
            if index + 1 >= len(parts):
                raise ValueError(f"--{key} requires a value")
            values[key] = parts[index + 1]
            index += 2
            continue
        values[f"_{position}"] = item
        position += 1
        index += 1
    return values


def _render_account_command(name: str, runtime: object) -> str:
    trace = _latest_trace(runtime)
    payload = {} if trace is None else _mapping(getattr(trace, "payload", None))
    if name == "account.current":
        return _render_account_current(payload.get("view"))
    if name == "account.balance":
        currency = payload.get("currency")
        return _render_balance(payload.get("balance"), title=f"account.balance {currency}")
    if name == "account.position":
        instrument = payload.get("instrument")
        return _render_position(payload.get("position"), title=f"account.position {instrument}")
    return f"ok {name}"


def _render_latest_intent(runtime: object) -> str:
    latest = getattr(getattr(runtime, "intents", None), "latest", lambda: None)()
    intent = getattr(latest, "intent", None)
    if intent is None:
        return "target_position: no intent"
    parts = [
        f"intent={getattr(intent, 'intent_id', None)}",
        f"instrument={getattr(intent, 'instrument_id', None)}",
        f"quantity={getattr(intent, 'target_quantity', None)}",
    ]
    account = getattr(intent, "account_id", None)
    if account is not None:
        parts.append(f"account={account}")
    limit_price = getattr(intent, "limit_price", None)
    if limit_price is not None:
        parts.append(f"limit_price={limit_price}")
    return "target_position: " + " ".join(parts)


def _latest_trace(runtime: object) -> object | None:
    views = getattr(runtime, "views", None)
    view = None if views is None else views.get("strategy.decision_trace", None)
    records = tuple(getattr(view, "records", ()) or ())
    return records[-1] if records else None


def _render_account_current(view: object) -> str:
    if view is None:
        return "account.current: none"
    lines = ["account.current"]
    summary = _account_summary_rows(view)
    if summary:
        lines.append(_table(("field", "value"), summary))
    balances = tuple(_field(view, "balances") or ())
    if balances:
        lines.append(_table(("currency", "total", "free", "locked", "source"), [_balance_row(item) for item in balances]))
    positions = tuple(_field(view, "positions") or ())
    if positions:
        lines.append(_table(("instrument", "quantity", "entry_price", "mark_price", "notional"), [_position_row(item) for item in positions]))
    if len(lines) == 1:
        lines.append(_compact(view))
    return "\n".join(lines)


def _render_balance(balance: object, *, title: str) -> str:
    if balance is None:
        return f"{title}: none"
    return title + "\n" + _table(("currency", "total", "free", "locked", "source"), (_balance_row(balance),))


def _render_position(position: object, *, title: str) -> str:
    if position is None:
        return f"{title}: none"
    return title + "\n" + _table(("instrument", "quantity", "entry_price", "mark_price", "notional"), (_position_row(position),))


def _account_summary_rows(view: object) -> list[tuple[str, object]]:
    rows: list[tuple[str, object]] = []
    for name in ("cash", "equity", "initial_equity", "net_profit", "total_return", "event_count", "last_event_time", "stale"):
        value = _field(view, name)
        if value is not None:
            rows.append((name, _cell(value)))
    return rows


def _balance_row(balance: object) -> tuple[object, ...]:
    return (
        _cell(_field(balance, "currency")),
        _cell(_field(balance, "total")),
        _cell(_field(balance, "free", _field(balance, "available"))),
        _cell(_field(balance, "locked")),
        _cell(_field(balance, "source")),
    )


def _position_row(position: object) -> tuple[object, ...]:
    return (
        _cell(_field(position, "instrument_id", _field(position, "instrument"))),
        _cell(_field(position, "quantity")),
        _cell(_field(position, "entry_price")),
        _cell(_field(position, "mark_price")),
        _cell(_field(position, "notional")),
    )


def _table(headers: tuple[str, ...], rows: object) -> str:
    table = PrettyTable()
    table.field_names = list(headers)
    table.align = "l"
    for row in rows:
        table.add_row([_cell(value) for value in row])
    return table.get_string()


def _field(value: object, name: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _cell(value: object) -> str:
    converted = _jsonable(value)
    if converted is None:
        return "-"
    if isinstance(converted, (str, int, float, bool)):
        return str(converted)
    return json.dumps(converted, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _compact(value: object) -> str:
    converted = _jsonable(value)
    if converted is None:
        return "none"
    if isinstance(converted, (str, int, float, bool)):
        return str(converted)
    return json.dumps(converted, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _jsonable(value: object) -> object:
    if is_dataclass(value):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "value") and isinstance(getattr(value, "value"), (str, int, float, bool)):
        return getattr(value, "value")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _json_object(value: str | None) -> Mapping[str, object]:
    if value is None:
        return {}
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError(f"expected JSON object: {error}") from error
    if not isinstance(payload, Mapping):
        raise ValueError("expected JSON object")
    return payload


def _without_none(values: Mapping[str, object | None]) -> dict[str, object]:
    return {key: value for key, value in values.items() if value is not None}


def _reject_extra(values: Mapping[str, object]) -> None:
    if values:
        raise ValueError(f"unsupported argument: {next(iter(values))}")


__all__ = [
    "InteractiveSystemSession",
    "parse_system_command",
    "parse_system_entry",
    "render_command_result",
    "render_system_result",
    "system_help_text",
]
