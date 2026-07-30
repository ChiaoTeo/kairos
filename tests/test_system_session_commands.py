from __future__ import annotations

import json

from kairospy.application.system.workspace import AccountLeaseManager
from kairospy.application.system.session import SystemCommand, SystemCommandDispatcher, SystemCommandFileQueue, SystemCommandResult
from kairospy.core.account import AccountIdentity


def test_system_command_file_queue_writes_pending_commands_and_responses(tmp_path) -> None:
    queue = SystemCommandFileQueue(tmp_path)

    command = queue.submit("account.current", {"account": "main"})
    pending = queue.pending()

    assert pending == (command,)
    assert json.loads(queue.command_path(command.command_id).read_text(encoding="utf-8"))["kind"] == "account.current"

    response_path = queue.respond(SystemCommandResult.accepted(command, {"cash": "100"}))

    assert json.loads(response_path.read_text(encoding="utf-8"))["status"] == "accepted"
    assert queue.pending() == ()


def test_system_command_dispatcher_queries_account_artifact(tmp_path) -> None:
    (tmp_path / "account").mkdir()
    (tmp_path / "account" / "current.json").write_text(
        json.dumps(
            {
                "launch_id": "paper-1",
                "mode": "paper",
                "account_view": {
                    "cash": "1000",
                    "equity": "1001",
                    "balances": [{"currency": "USDT", "total": "1000", "free": "1000", "locked": "0"}],
                    "positions": [{"instrument_id": "market:binance:spot:btc_usdt", "quantity": "0.01"}],
                    "open_orders": [{"order_id": "venue-order-1"}],
                    "pending_orders": [{"order_id": "local-order-1"}],
                },
            }
        ),
        encoding="utf-8",
    )
    dispatcher = SystemCommandDispatcher(tmp_path)

    current = dispatcher.dispatch(SystemCommand.create("account.current"))
    balances = dispatcher.dispatch(SystemCommand.create("account.balances"))
    positions = dispatcher.dispatch(SystemCommand.create("account.positions"))

    assert current.status == "accepted"
    assert current.result["current"]["cash"] == "1000"
    assert balances.result["balances"][0]["currency"] == "USDT"
    assert positions.result["positions"][0]["instrument_id"] == "market:binance:spot:btc_usdt"


def test_system_command_dispatcher_queries_order_status_from_timeline_views(tmp_path) -> None:
    (tmp_path / "timeline.jsonl").write_text(
        json.dumps(
            {
                "views": {
                    "execution.current": {
                        "payload": {
                            "latest_order": {"order_id": "order-1", "status": "filled"},
                            "orders": [
                                {"order_id": "order-1", "status": "filled", "filled_quantity": "0.01"},
                            ],
                        }
                    }
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    dispatcher = SystemCommandDispatcher(tmp_path)

    result = dispatcher.dispatch(SystemCommand.create("order.status", {"order_id": "order-1", "account": "main"}))

    assert result.status == "accepted"
    assert result.result["account"] == "main"
    assert result.result["status"] == "filled"
    assert result.result["order"]["filled_quantity"] == "0.01"


def test_system_command_dispatcher_reports_account_trade_status(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_account(tmp_path)
    instance = tmp_path / ".kairos" / "launches" / "system" / "kairos-system" / "instances" / "system-1"
    instance.mkdir(parents=True)
    (instance / "state.json").write_text(json.dumps({"launch_instance_id": "system-1"}), encoding="utf-8")

    result = SystemCommandDispatcher(instance).dispatch(SystemCommand.create("account.trade-status"))

    assert result.status == "accepted"
    assert result.result["count"] == 1
    account = result.result["accounts"][0]
    assert account["account"] == "main"
    assert account["trade_state"] == "available"
    assert account["can_trade"] is True
    assert account["tradable_books"] == ["spot"]


def test_system_command_dispatcher_reports_occupied_trade_status(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_account(tmp_path)
    instance = tmp_path / ".kairos" / "launches" / "system" / "kairos-system" / "instances" / "system-1"
    instance.mkdir(parents=True)
    (instance / "state.json").write_text(json.dumps({"launch_instance_id": "system-1"}), encoding="utf-8")
    AccountLeaseManager(tmp_path / ".kairos" / "state" / "account-locks").acquire(
        AccountIdentity("binance", "main"),
        environment="paper",
        launch_id="trader",
        launch_instance_id="trader-1",
        mode="paper",
    )

    result = SystemCommandDispatcher(instance).dispatch(SystemCommand.create("account.trade-status", {"account": "main"}))

    account = result.result["accounts"][0]
    assert account["trade_state"] == "occupied"
    assert account["can_trade"] is False
    assert account["lock"]["launch_id"] == "trader"


def test_system_command_dispatcher_reports_system_owned_trade_status(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_account(tmp_path)
    instance = tmp_path / ".kairos" / "launches" / "system" / "kairos-system" / "instances" / "system-1"
    instance.mkdir(parents=True)
    (instance / "state.json").write_text(json.dumps({"launch_instance_id": "system-1"}), encoding="utf-8")
    AccountLeaseManager(tmp_path / ".kairos" / "state" / "account-locks").acquire(
        AccountIdentity("binance", "main"),
        environment="paper",
        launch_id="kairos-system",
        launch_instance_id="system-1",
        mode="system",
    )

    result = SystemCommandDispatcher(instance).dispatch(SystemCommand.create("account.trade-status"))

    account = result.result["accounts"][0]
    assert account["trade_state"] == "owned"
    assert account["can_trade"] is True


def test_system_command_dispatcher_acquires_and_releases_trade_lock(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_account(tmp_path)
    instance = tmp_path / ".kairos" / "launches" / "system" / "kairos-system" / "instances" / "system-1"
    instance.mkdir(parents=True)
    (instance / "state.json").write_text(json.dumps({"launch_instance_id": "system-1"}), encoding="utf-8")
    dispatcher = SystemCommandDispatcher(instance)

    acquired = dispatcher.dispatch(SystemCommand.create("account.trade-acquire", {"account": "main"}))
    released = dispatcher.dispatch(SystemCommand.create("account.trade-release", {"account": "main"}))

    assert acquired.status == "accepted"
    assert acquired.result["acquired"] is True
    assert acquired.result["trade_state"] == "owned"
    assert released.status == "accepted"
    assert released.result["released"] is True
    assert AccountLeaseManager(tmp_path / ".kairos" / "state" / "account-locks").get("binance.main") is None


def test_system_command_dispatcher_trade_acquire_reports_occupied(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_account(tmp_path)
    instance = tmp_path / ".kairos" / "launches" / "system" / "kairos-system" / "instances" / "system-1"
    instance.mkdir(parents=True)
    (instance / "state.json").write_text(json.dumps({"launch_instance_id": "system-1"}), encoding="utf-8")
    AccountLeaseManager(tmp_path / ".kairos" / "state" / "account-locks").acquire(
        AccountIdentity("binance", "main"),
        environment="paper",
        launch_id="trader",
        launch_instance_id="trader-1",
        mode="paper",
    )

    result = SystemCommandDispatcher(instance).dispatch(SystemCommand.create("account.trade-acquire", {"account": "main"}))

    assert result.status == "accepted"
    assert result.result["acquired"] is False
    assert result.result["trade_state"] == "occupied"
    assert result.result["lock"]["launch_id"] == "trader"


def _write_workspace_account(root) -> None:
    kairos = root / ".kairos"
    accounts = kairos / "accounts"
    accounts.mkdir(parents=True, exist_ok=True)
    (kairos / "kairos.toml").write_text("[project]\nname = \"test\"\n", encoding="utf-8")
    (accounts / "main.toml").write_text(
        "\n".join(
            [
                "[account]",
                'id = "main"',
                'provider = "binance"',
                'environment = "paper"',
                'venue = "binance"',
                'market = "spot"',
                "",
                "[books.spot]",
                'kind = "spot"',
                "",
            ]
        ),
        encoding="utf-8",
    )
