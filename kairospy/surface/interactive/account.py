from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from kairospy.application.system.facade.account import ACCOUNT_SCHEMAS, AccountFacade


_ENVIRONMENTS = ("paper", "testnet", "live")
_CREDENTIAL_MODES = ("reference", "direct", "skip")


@dataclass(slots=True)
class AccountCreateWizard:
    facade: AccountFacade = field(default_factory=AccountFacade)
    fields: dict[str, str | None] = field(default_factory=dict)
    credential_fields: dict[str, str] = field(default_factory=dict)
    current: str = "account_id"
    complete: bool = False
    canceled: bool = False
    result_path: str | None = None
    error: str | None = None

    def start(self) -> str:
        return "\n".join(
            [
                "Account create wizard",
                "Type `cancel` to stop. Press Enter to accept defaults shown in brackets.",
                self.prompt(),
            ]
        )

    def prompt(self) -> str:
        match self.current:
            case "account_id":
                return "account id: "
            case "provider":
                return f"broker [{'/'.join(sorted(ACCOUNT_SCHEMAS))}]: "
            case "environment":
                return "environment [paper/testnet/live] (paper): "
            case "currency":
                return "currency (USD): "
            case "cash":
                return "initial cash (100000): "
            case "fee_rate":
                return "fee rate (0): "
            case "credential_mode":
                return "credential [reference/direct/skip] (reference): "
            case "credential":
                return "credential id, for example okx_live: "
            case field_name if field_name.startswith("credential."):
                name = field_name.split(".", 1)[1]
                optional = name in ACCOUNT_SCHEMAS[self._provider()].optional_fields
                suffix = " (optional): " if optional else ": "
                return f"{name}{suffix}"
            case "confirm":
                return self._summary() + "\ncreate account? [y/N]: "
            case _:
                return ""

    def handle(self, line: str) -> str:
        value = line.strip()
        if value.lower() in {"cancel", "quit", "exit"}:
            self.canceled = True
            self.complete = True
            return "Account create canceled."
        try:
            self._accept(value)
        except ValueError as error:
            return f"error: {error}\n{self.prompt()}"
        if self.complete:
            if self.error is not None:
                return f"error: {self.error}"
            if self.result_path is not None:
                return f"created account: {self.result_path}"
            return "Account create canceled."
        return self.prompt()

    def _accept(self, value: str) -> None:
        match self.current:
            case "account_id":
                if not value:
                    raise ValueError("account id is required")
                self.fields["account_id"] = value
                self.current = "provider"
            case "provider":
                provider = value.lower().replace("-", "_")
                if not provider:
                    raise ValueError("broker is required")
                if provider not in ACCOUNT_SCHEMAS:
                    raise ValueError(f"unsupported broker: {value}; supported: {', '.join(sorted(ACCOUNT_SCHEMAS))}")
                self.fields["provider"] = provider
                self.current = "environment"
            case "environment":
                environment = value.lower() or "paper"
                if environment not in _ENVIRONMENTS:
                    raise ValueError(f"environment must be one of: {', '.join(_ENVIRONMENTS)}")
                self.fields["environment"] = environment
                self.current = "currency"
            case "currency":
                self.fields["currency"] = value.upper() if value else "USD"
                self.current = "cash" if self._environment() != "live" else "credential_mode"
            case "cash":
                self.fields["cash"] = value or "100000"
                self.current = "fee_rate"
            case "fee_rate":
                self.fields["fee_rate"] = value or "0"
                self.current = "credential_mode" if self._environment() == "testnet" else "confirm"
            case "credential_mode":
                mode = value.lower() or "reference"
                if mode not in _CREDENTIAL_MODES:
                    raise ValueError(f"credential mode must be one of: {', '.join(_CREDENTIAL_MODES)}")
                self.fields["credential_mode"] = mode
                if mode == "reference":
                    self.current = "credential"
                elif mode == "direct":
                    self.current = self._first_credential_field()
                else:
                    self.current = "confirm"
            case "credential":
                if not value:
                    raise ValueError("credential reference is required")
                self.fields["credential"] = value
                self.current = "confirm"
            case field_name if field_name.startswith("credential."):
                name = field_name.split(".", 1)[1]
                schema = ACCOUNT_SCHEMAS[self._provider()]
                if not value and name not in schema.optional_fields:
                    raise ValueError(f"{name} is required")
                if value:
                    self.credential_fields[name] = value
                self.current = self._next_credential_field(name)
            case "confirm":
                if value.lower() not in {"y", "yes"}:
                    self.canceled = True
                    self.complete = True
                    return
                self._create()
            case _:
                raise ValueError("wizard is in an invalid state")

    def _create(self) -> None:
        try:
            self.result_path = self.facade.create(
                account_id=self._required("account_id"),
                broker=self._required("provider"),
                environment=self._required("environment"),
                venue=None,
                market=None,
                currency=self._required("currency"),
                cash=self.fields.get("cash"),
                fee_rate=self.fields.get("fee_rate") or "0",
                credential_kind=None,
                credential=self.fields.get("credential"),
                credential_role="readonly",
                api_key=self.credential_fields.get("api_key"),
                api_secret=self.credential_fields.get("api_secret"),
                passphrase=self.credential_fields.get("passphrase"),
                wallet_address=self.credential_fields.get("wallet_address"),
                private_key=self.credential_fields.get("private_key"),
                vault_address=self.credential_fields.get("vault_address"),
                field_values=None,
                credential_values=None,
                force=False,
            )
        except ValueError as error:
            self.error = str(error)
        self.complete = True

    def _provider(self) -> str:
        return self._required("provider")

    def _environment(self) -> str:
        return self._required("environment")

    def _required(self, key: str) -> str:
        value = self.fields.get(key)
        if value is None:
            raise ValueError(f"{key} is required")
        return value

    def _first_credential_field(self) -> str:
        schema = ACCOUNT_SCHEMAS[self._provider()]
        return f"credential.{schema.credential_fields[0]}"

    def _next_credential_field(self, name: str) -> str:
        fields = ACCOUNT_SCHEMAS[self._provider()].credential_fields
        index = fields.index(name)
        if index + 1 >= len(fields):
            return "confirm"
        return f"credential.{fields[index + 1]}"

    def _summary(self) -> str:
        lines = [
            "Account summary",
            f"  id:          {self.fields.get('account_id')}",
            f"  broker:      {self.fields.get('provider')}",
            f"  environment: {self.fields.get('environment')}",
            f"  currency:    {self.fields.get('currency')}",
        ]
        if self.fields.get("cash") is not None:
            lines.append(f"  cash:        {self.fields.get('cash')}")
        if self.fields.get("fee_rate") is not None:
            lines.append(f"  fee_rate:    {self.fields.get('fee_rate')}")
        if self.fields.get("credential"):
            lines.append(f"  credential:  {self.fields.get('credential')}")
        elif self.credential_fields:
            fields = ", ".join(sorted(self.credential_fields))
            lines.append(f"  credential:  direct fields ({fields})")
        else:
            lines.append("  credential:  none")
        return "\n".join(lines)


def run_account_create_wizard(
    *,
    prompt: Callable[[str], str],
    echo: Callable[[str], None],
    facade: AccountFacade | None = None,
) -> int:
    wizard = AccountCreateWizard(facade=facade or AccountFacade())
    message = wizard.start()
    while not wizard.complete:
        answer = prompt(message)
        message = wizard.handle(answer)
    echo(message)
    return 1 if wizard.error is not None else 0


def is_account_create_argv(argv: list[str]) -> bool:
    return len(argv) >= 2 and argv[:2] == ["account", "create"]


def account_create_direct_argv(argv: list[str]) -> list[str] | None:
    if not is_account_create_argv(argv) or "--direct" not in argv:
        return None
    return [item for item in argv if item != "--direct"]


__all__ = [
    "AccountCreateWizard",
    "account_create_direct_argv",
    "is_account_create_argv",
    "run_account_create_wizard",
]
