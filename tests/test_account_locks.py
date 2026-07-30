from __future__ import annotations

from pathlib import Path

import pytest

from kairospy.application.launch.launcher import TradingSystemLauncher
from kairospy.application.launch.host.resources import TradingLaunchSpec
from kairospy.application.launch.host.runtime_host import TradingSystem
from kairospy.application.modes import RuntimeMode
from kairospy.application.strategy import CliStrategyBase, cli_command_envelope
from kairospy.application.system.workspace import AccountLeaseError, AccountLeaseManager
from kairospy.core.account import AccountIdentity
from kairospy.core.intent import IntentStatus


def test_account_lease_manager_blocks_concurrent_writer(tmp_path: Path) -> None:
    manager = AccountLeaseManager(tmp_path / "locks")
    identity = AccountIdentity("binance", "main")
    lease = manager.acquire(identity, environment="paper", launch_id="first", launch_instance_id="instance-1", mode="paper")

    with pytest.raises(AccountLeaseError, match="trading is already leased"):
        manager.acquire(identity, environment="paper", launch_id="second", launch_instance_id="instance-2", mode="paper")

    assert manager.get("binance.main") is not None
    lease.release()
    assert manager.get("binance.main") is None


def test_paper_launch_respects_account_trade_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_project(tmp_path)
    _write_account(tmp_path, "main")
    config = _write_paper_config(tmp_path, account_ref="main")
    manager = AccountLeaseManager(tmp_path / ".kairos" / "state" / "account-locks")
    lease = manager.acquire(AccountIdentity("binance", "main"), environment="paper", launch_id="other", launch_instance_id="other-1", mode="paper")

    with pytest.raises(AccountLeaseError, match="trading is already leased"):
        TradingSystemLauncher().launch_paper_config(config)

    lease.release()


def test_read_only_paper_launch_can_reference_locked_account(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_project(tmp_path)
    _write_account(tmp_path, "main")
    config = _write_paper_config(tmp_path, account_ref="main", trade=False)
    manager = AccountLeaseManager(tmp_path / ".kairos" / "state" / "account-locks")
    lease = manager.acquire(AccountIdentity("binance", "main"), environment="paper", launch_id="trader", launch_instance_id="trader-1", mode="paper")

    result = TradingSystemLauncher().launch_paper_config(config)

    assert result.mode.value == "paper"
    assert manager.get("binance.main").launch_id == "trader"
    capabilities = result.views.require("account.capabilities").capabilities
    assert len(capabilities) == 1
    assert capabilities[0].can_trade is False
    lease.release()


def test_non_trading_book_reference_skips_trade_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_project(tmp_path)
    _write_account(tmp_path, "main")
    config = _write_paper_config(tmp_path, account_ref="main", books=("funding",))
    manager = AccountLeaseManager(tmp_path / ".kairos" / "state" / "account-locks")
    lease = manager.acquire(AccountIdentity("binance", "main"), environment="paper", launch_id="trader", launch_instance_id="trader-1", mode="paper")

    result = TradingSystemLauncher().launch_paper_config(config)

    assert result.views.require("account.capabilities").capabilities[0].can_trade is False
    lease.release()


def test_system_runtime_sees_account_when_trade_lock_is_held_by_other(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_project(tmp_path)
    _write_account(tmp_path, "main")
    manager = AccountLeaseManager(tmp_path / ".kairos" / "state" / "account-locks")
    lease = manager.acquire(AccountIdentity("binance", "main"), environment="paper", launch_id="trader", launch_instance_id="trader-1", mode="paper")

    runtime, authority = _start_system_runtime(tmp_path)

    try:
        books = runtime.views.require("account.books").books
        capabilities = runtime.views.require("account.capabilities").capabilities
        assert len(books) == 1
        assert books[0].account_id == "main"
        assert capabilities[0].can_trade is False
        assert manager.get("binance.main").launch_id == "trader"
    finally:
        authority.release()
        runtime.close()
        lease.release()


def test_system_runtime_rejects_target_position_without_trade_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_project(tmp_path)
    _write_account(tmp_path, "main")
    manager = AccountLeaseManager(tmp_path / ".kairos" / "state" / "account-locks")
    lease = manager.acquire(AccountIdentity("binance", "main"), environment="paper", launch_id="trader", launch_instance_id="trader-1", mode="paper")
    runtime, authority = _start_system_runtime(tmp_path)

    try:
        runtime.process(
            cli_command_envelope(
                "target_position",
                {"account": "main", "instrument": "binance:spot:BTC/USDT", "quantity": "0.1", "intent_id": "intent-locked"},
            )
        )
        state = runtime.intents.latest()
        assert state.intent.intent_id == "intent-locked"
        assert state.status is IntentStatus.REJECTED
        assert "trading is locked by trader" in state.reason
    finally:
        authority.release()
        runtime.close()
        lease.release()


def test_system_runtime_reacquires_trade_lock_after_other_owner_releases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_project(tmp_path)
    _write_account(tmp_path, "main")
    manager = AccountLeaseManager(tmp_path / ".kairos" / "state" / "account-locks")
    lease = manager.acquire(AccountIdentity("binance", "main"), environment="paper", launch_id="trader", launch_instance_id="trader-1", mode="paper")
    runtime, authority = _start_system_runtime(tmp_path)

    try:
        assert runtime.views.require("account.capabilities").capabilities[0].can_trade is False
        lease.release()
        runtime.process(cli_command_envelope("account.current", {"account": "main"}))

        lock = manager.get("binance.main")
        assert lock is not None
        assert lock.launch_id == "kairos-system"
        assert lock.environment == "paper"
        assert runtime.views.require("account.capabilities").capabilities[0].can_trade is True
    finally:
        authority.release()
        runtime.close()


def test_system_runtime_acquires_free_trade_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_project(tmp_path)
    _write_account(tmp_path, "main")
    runtime, authority = _start_system_runtime(tmp_path)

    try:
        lock = AccountLeaseManager(tmp_path / ".kairos" / "state" / "account-locks").get("binance.main")
        assert lock is not None
        assert lock.launch_id == "kairos-system"
        assert runtime.views.require("account.capabilities").capabilities[0].can_trade is True
    finally:
        authority.release()
        runtime.close()


def test_system_runtime_reacquires_after_own_cached_lock_is_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_project(tmp_path)
    _write_account(tmp_path, "main")
    manager = AccountLeaseManager(tmp_path / ".kairos" / "state" / "account-locks")
    runtime, authority = _start_system_runtime(tmp_path)

    try:
        first = manager.get("binance.main")
        assert first is not None
        assert first.launch_id == "kairos-system"
        manager.release("binance.main", launch_instance_id=first.launch_instance_id)

        assert manager.get("binance.main") is None
        runtime.process(cli_command_envelope("account.current", {"account": "main"}))
        assert runtime.views.require("account.capabilities").capabilities[0].can_trade is True

        reacquired = manager.get("binance.main")
        assert reacquired is not None
        assert reacquired.launch_id == "kairos-system"
        assert reacquired.launch_instance_id == first.launch_instance_id
    finally:
        authority.release()
        runtime.close()


def test_system_runtime_stops_trading_when_cached_lock_is_taken_by_other(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_project(tmp_path)
    _write_account(tmp_path, "main")
    manager = AccountLeaseManager(tmp_path / ".kairos" / "state" / "account-locks")
    runtime, authority = _start_system_runtime(tmp_path)

    try:
        first = manager.get("binance.main")
        assert first is not None
        manager.release("binance.main", launch_instance_id=first.launch_instance_id)
        other = manager.acquire(AccountIdentity("binance", "main"), environment="paper", launch_id="trader", launch_instance_id="trader-1", mode="paper")

        runtime.process(
            cli_command_envelope(
                "target_position",
                {"account": "main", "instrument": "binance:spot:BTC/USDT", "quantity": "0.1", "intent_id": "intent-retaken"},
            )
        )

        state = runtime.intents.latest()
        assert state.intent.intent_id == "intent-retaken"
        assert state.status is IntentStatus.REJECTED
        assert "trading is locked by trader" in state.reason
        other.release()
    finally:
        authority.release()
        runtime.close()


def _write_project(root: Path) -> None:
    project = root / ".kairos"
    project.mkdir()
    (project / "kairos.toml").write_text("[project]\nname = \"locks\"\n", encoding="utf-8")


def _write_account(root: Path, account_id: str) -> None:
    accounts = root / ".kairos" / "accounts"
    accounts.mkdir(parents=True, exist_ok=True)
    (accounts / f"{account_id}.toml").write_text(
        "\n".join(
            [
                "[account]",
                f"id = \"{account_id}\"",
                "provider = \"binance\"",
                "environment = \"paper\"",
                "venue = \"binance\"",
                "market = \"spot\"",
                "cash = \"100000\"",
                "currency = \"USDT\"",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_paper_config(root: Path, *, account_ref: str, trade: bool = True, books: tuple[str, ...] = ()) -> Path:
    events = root / "events.jsonl"
    events.write_text('{"domain":"market","kind":"noop","time":"2026-01-01T00:00:00+00:00"}\n', encoding="utf-8")
    (root / "strategy_mod.py").write_text(
        "\n".join(
            [
                "from kairospy.application.strategy import StrategyBase",
                "",
                "class ConfiguredStrategy(StrategyBase):",
                "    strategy_id = 'lock-test-strategy'",
                "    def on_data(self, context, signal):",
                "        return None",
                "",
            ]
        ),
        encoding="utf-8",
    )
    config = root / "paper.toml"
    config.write_text(
        "\n".join(
            [
                "[launch]",
                "id = \"paper-system\"",
                "mode = \"paper\"",
                "strategy = \"strategy_mod:ConfiguredStrategy\"",
                "",
                "[accounts.main]",
                f"ref = \"{account_ref}\"",
                f"trade = {str(trade).lower()}",
                *([f"books = [{', '.join(repr(book) for book in books)}]"] if books else []),
                "",
                "[paper]",
                f"events = \"{events}\"",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return config


def _start_system_runtime(root: Path):
    launcher = TradingSystemLauncher()
    launch_directory = root / ".kairos" / "launches" / "system" / "kairos-system" / "instances" / "test-system"
    resources, authority = launcher._system_resources(launch_directory, launch_id="kairos-system")
    runtime = TradingSystem(
        TradingLaunchSpec(
            launch_id="kairos-system",
            mode=RuntimeMode.SYSTEM,
            strategy=CliStrategyBase(),
            launch_directory=launch_directory,
            normalized_config={"launch": {"id": "kairos-system", "mode": "system"}},
            resources=resources,
        )
    ).start()
    return runtime, authority
