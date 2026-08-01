from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping

from kairospy.application.protocol import RuntimeEnvelope, system_envelope
from kairospy.core.intent import TradeIntent

from .context import Context
from .protocol import StrategyBase


@dataclass(frozen=True, slots=True)
class CliCommand:
    name: str
    args: Mapping[str, object]

    @classmethod
    def from_payload(cls, payload: object) -> "CliCommand":
        if isinstance(payload, Mapping):
            name = str(payload.get("command") or payload.get("name") or "").strip()
            if not name:
                raise ValueError("cli command payload requires command")
            args = payload.get("args")
            if args is None:
                args = {key: value for key, value in payload.items() if key not in {"command", "name"}}
            if not isinstance(args, Mapping):
                raise ValueError("cli command args must be an object")
            return cls(name, dict(args))
        raise ValueError("cli command payload must be an object")


class CliStrategyBase(StrategyBase):
    """Strategy test driver that turns CLI command events into normal strategy actions."""

    strategy_id = "cli-strategy"

    def on_system(self, context: Context, signal: object) -> None:
        if not _is_cli_command(signal):
            return None
        self.on_cli_command(context, CliCommand.from_payload(getattr(signal, "payload", None)), signal)
        return None

    def on_cli_command(self, context: Context, command: CliCommand, signal: object) -> None:
        if command.name == "trace":
            context.trace(str(command.args.get("name") or "cli"), dict(command.args.get("payload") or {}))
            return None
        if command.name == "target_position":
            self.target_position(context, command)
            return None
        if command.name == "account.current":
            view = context.accounts.current(_optional_text(command.args.get("account")))
            context.trace("account.current", {"account": command.args.get("account"), "view": view})
            return None
        if command.name == "account.balance":
            balance = context.accounts.balance(str(command.args.get("currency") or ""), account=_optional_text(command.args.get("account")))
            context.trace("account.balance", {"account": command.args.get("account"), "currency": command.args.get("currency"), "balance": balance})
            return None
        if command.name == "account.position":
            instrument = command.args.get("instrument")
            if instrument is None:
                raise ValueError("account.position requires instrument")
            position = context.accounts.position(instrument, account=_optional_text(command.args.get("account")))
            context.trace("account.position", {"account": command.args.get("account"), "instrument": instrument, "position": position})
            return None
        raise ValueError(f"unsupported cli strategy command: {command.name}")

    def target_position(self, context: Context, command: CliCommand) -> TradeIntent:
        instrument = command.args.get("instrument")
        if instrument is None:
            raise ValueError("target_position requires instrument")
        quantity = command.args.get("quantity")
        if quantity is None:
            raise ValueError("target_position requires quantity")
        return context.target_position(
            instrument,
            Decimal(str(quantity)),
            account=_optional_text(command.args.get("account")),
            book=command.args.get("book"),
            limit_price=None if command.args.get("limit_price") is None else Decimal(str(command.args["limit_price"])),
            reason=str(command.args.get("reason") or "cli target_position"),
            intent_id=_optional_text(command.args.get("intent_id")),
        )


def cli_command_envelope(
    command: str,
    args: Mapping[str, object] | None = None,
    *,
    time: datetime | None = None,
    sequence: int = 1,
) -> RuntimeEnvelope:
    return system_envelope(
        "cli.command",
        time=time or datetime.now(timezone.utc),
        sequence=sequence,
        payload={"command": command, "args": dict(args or {})},
    )


def _is_cli_command(signal: object) -> bool:
    return getattr(signal, "domain", None) == "system" and getattr(signal, "kind", None) == "cli.command"


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = ["CliCommand", "CliStrategyBase", "cli_command_envelope"]
