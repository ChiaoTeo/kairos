from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace

import kairospy.surface.interactive.shell as shell_product
import pytest
from kairospy.core.intent import IntentJournal, target_position_intent
from kairospy.application.modes import RuntimeMode
from kairospy.surface.cli.app import execute_argv
from kairospy.surface.interactive.shell import AppSession
from kairospy.surface.interactive.system import parse_system_command, parse_system_entry, render_command_result


def test_system_shell_entry_uses_builtin_system_strategy() -> None:
    entry = parse_system_entry(["--launch-id", "check-1"])

    assert entry == {"launch_id": "check-1"}


def test_system_shell_entry_rejects_strategy_and_mode_overrides() -> None:
    with pytest.raises(ValueError, match="built-in CliStrategyBase"):
        parse_system_entry(["--strategy", "strategy:factory"])
    with pytest.raises(ValueError, match="does not accept --mode"):
        parse_system_entry(["--mode", "paper"])


def test_system_shell_command_parses_domain_commands() -> None:
    assert parse_system_command("account balance USDT --account main") == {
        "command": "account.balance",
        "args": {"account": "main", "currency": "USDT"},
    }
    assert parse_system_command("order target-position BTC/USDT 0.1 --account main --limit-price 50000") == {
        "command": "target_position",
        "args": {"account": "main", "instrument": "BTC/USDT", "limit_price": "50000", "quantity": "0.1"},
    }


def test_app_shell_enters_system_and_reuses_session(monkeypatch) -> None:
    created: list[FakeInteractiveSystemSession] = []

    def factory(**kwargs):
        session = FakeInteractiveSystemSession(**kwargs)
        created.append(session)
        return session

    monkeypatch.setattr(shell_product, "InteractiveSystemSession", factory)
    stdout = StringIO()
    session = AppSession(stdout=stdout, command_executor=lambda argv: (0, ""))

    assert session.handle("system --launch-id check-1") is False
    assert session.prompt() == "kairos/app/system> "
    assert session.handle("account current --account main") is False
    assert session.handle("order target-position BTC/USDT 0.1 --account main") is False
    assert session.handle("exit-system") is False

    assert session.system_session is None
    assert created[0].kwargs == {"launch_id": "check-1"}
    assert created[0].lines == ["account current --account main", "order target-position BTC/USDT 0.1 --account main"]
    assert created[0].finished is True
    assert created[0].closed is True
    assert "Entered system mode" in stdout.getvalue()
    assert "launch_id: check-1" in stdout.getvalue()


def test_cli_shell_command_enters_system_mode(tmp_path, monkeypatch) -> None:
    _write_workspace_manifest(tmp_path)
    monkeypatch.chdir(tmp_path)
    created: list[FakeInteractiveSystemSession] = []

    def factory(**kwargs):
        session = FakeInteractiveSystemSession(**kwargs)
        created.append(session)
        return session

    monkeypatch.setattr(shell_product, "InteractiveSystemSession", factory)
    stdout = StringIO()

    exit_code = execute_argv(
        [
            "shell",
            "--command",
            "system --launch-id check-1",
            "--command",
            "trace check '{}'",
            "--command",
            "exit-system",
        ],
        stdout,
    )

    assert exit_code == 0
    assert created[0].lines == ["trace check '{}'"]
    assert "Entered system mode" in stdout.getvalue()
    assert "launch_id: check-1" in stdout.getvalue()


def test_system_command_result_renders_trace_payload() -> None:
    runtime = SimpleNamespace(
        views=FakeViews(SimpleNamespace(records=(SimpleNamespace(name="check", payload={"ok": True}),)))
    )

    assert render_command_result({"command": "trace"}, runtime) == 'check: {"ok":true}'


def test_system_command_result_renders_account_balance_trace() -> None:
    runtime = SimpleNamespace(
        views=FakeViews(
            SimpleNamespace(
                records=(
                    SimpleNamespace(
                        name="account.balance",
                        payload={"currency": "USDT", "balance": {"available": "100", "total": "100"}},
                    ),
                )
            )
        )
    )

    output = render_command_result({"command": "account.balance"}, runtime)

    assert "account.balance USDT" in output
    assert "currency" in output
    assert "USDT" in output
    assert "100" in output


def test_system_command_result_renders_account_current_tables() -> None:
    runtime = SimpleNamespace(
        views=FakeViews(
            SimpleNamespace(
                records=(
                    SimpleNamespace(
                        name="account.current",
                        payload={
                            "view": {
                                "cash": "1000",
                                "equity": "1100",
                                "balances": (
                                    {"currency": "USDT", "total": "1000", "free": "900", "locked": "100", "source": "venue"},
                                ),
                                "positions": (
                                    {"instrument_id": "BTC/USDT", "quantity": "0.1", "mark_price": "50000", "notional": "5000"},
                                ),
                            }
                        },
                    ),
                )
            )
        )
    )

    output = render_command_result({"command": "account.current"}, runtime)

    assert "account.current" in output
    assert "cash" in output
    assert "equity" in output
    assert "USDT" in output
    assert "BTC/USDT" in output


def test_system_command_result_renders_latest_target_position_intent() -> None:
    journal = IntentJournal()
    intent = target_position_intent(
        strategy_id="strategy",
        instrument_id="BTC/USDT",
        target_quantity=Decimal("0.1"),
        account_id="main",
        limit_price=Decimal("50000"),
        intent_id="intent-1",
    )
    journal.record_intent(intent, at=datetime(2026, 1, 1, tzinfo=timezone.utc))

    assert render_command_result({"command": "target_position"}, SimpleNamespace(intents=journal)) == (
        "target_position: intent=intent-1 instrument=BTC/USDT quantity=0.1 account=main limit_price=50000"
    )


class FakeInteractiveSystemSession:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.lines: list[str] = []
        self.finished = False
        self.closed = False

    def handle(self, line: str) -> str:
        self.lines.append(line)
        return "ok"

    def finish(self):
        self.finished = True
        return SimpleNamespace(
            launch_id=self.kwargs["launch_id"],
            mode=self.kwargs.get("mode", RuntimeMode.SYSTEM),
            runtime=SimpleNamespace(strategy_id="strategy", event_count=len(self.lines), intent_count=1),
        )

    def close(self) -> None:
        self.closed = True


class FakeViews:
    def __init__(self, value) -> None:
        self.value = value

    def get(self, key, default=None):
        return self.value if key == "strategy.decision_trace" else default


def _write_workspace_manifest(root) -> None:
    kairos = root / ".kairos"
    kairos.mkdir(parents=True, exist_ok=True)
    (kairos / "kairos.toml").write_text("[project]\nname = \"test\"\n", encoding="utf-8")
