from __future__ import annotations

import json

from typer.testing import CliRunner

import kairospy.surface.cli.commands.order as order_product
from kairospy.surface.cli.commands.order import order_app


def test_order_place_defaults_to_dry_launch_and_writes_journal(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    account_root = tmp_path / ".kairos" / "accounts"
    account_root.mkdir(parents=True)
    (account_root / "binance_testnet.toml").write_text(
        "\n".join(
            [
                "[account]",
                'provider = "binance"',
                'environment = "testnet"',
                'venue = "binance"',
            ]
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        order_app,
        [
            "place",
            "--account",
            "binance_testnet",
            "--symbol",
            "BTC/USDT",
            "--side",
            "buy",
            "--qty",
            "0.01",
            "--price",
            "50000",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert payload["request"]["account"] == "binance_testnet"
    assert payload["request"]["amount"] == "0.01"
    journal = tmp_path / ".kairos" / "orders" / "journals" / "binance_testnet.jsonl"
    assert json.loads(journal.read_text(encoding="utf-8"))["action"] == "place_dry_run"
    operations = (tmp_path / ".kairos" / "state" / "operations.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(operations[-1])["action"] == "order.place_dry_run"


def test_live_order_submit_requires_confirmation(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    account_root = tmp_path / ".kairos" / "accounts"
    account_root.mkdir(parents=True)
    (account_root / "binance_live.toml").write_text(
        "\n".join(
            [
                "[account]",
                'provider = "binance"',
                'environment = "live"',
                'venue = "binance"',
            ]
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        order_app,
        [
            "cancel",
            "--account",
            "binance_live",
            "--order-id",
            "abc",
            "--submit",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code != 0
    assert "live order submission requires --confirm-live" in result.output


def test_order_submit_requires_trade_credential(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    account_root = tmp_path / ".kairos" / "accounts"
    account_root.mkdir(parents=True)
    (account_root / "binance_testnet.toml").write_text(
        "\n".join(
            [
                "[account]",
                'broker = "binance"',
                'environment = "testnet"',
                "",
                "[credentials.readonly]",
                'ref = "binance_read"',
            ]
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        order_app,
        [
            "place",
            "--account",
            "binance_testnet",
            "--symbol",
            "BTC/USDT",
            "--side",
            "buy",
            "--qty",
            "0.01",
            "--submit",
        ],
    )

    assert result.exit_code != 0
    assert "has no trade credential" in result.output


def test_order_show_reads_local_order_journal(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    account_root = tmp_path / ".kairos" / "accounts"
    account_root.mkdir(parents=True)
    (account_root / "binance_testnet.toml").write_text(
        "\n".join(
            [
                "[account]",
                'provider = "binance"',
                'environment = "testnet"',
                'venue = "binance"',
            ]
        ),
        encoding="utf-8",
    )
    cancel = CliRunner().invoke(
        order_app,
        [
            "cancel",
            "--account",
            "binance_testnet",
            "--order-id",
            "abc",
        ],
        catch_exceptions=False,
    )

    result = CliRunner().invoke(
        order_app,
        ["inspect", "--account", "binance_testnet", "--order-id", "abc"],
        catch_exceptions=False,
    )

    assert cancel.exit_code == 0
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["count"] == 1
    assert payload["records"][0]["request"]["order_id"] == "abc"


def test_order_place_with_launch_uses_system_command_channel(monkeypatch) -> None:
    calls = []

    class FakeLaunches:
        def submit_command(self, **kwargs):
            calls.append(kwargs)
            return {"kind": kwargs["kind"], "command_id": "command-1"}

    monkeypatch.setattr(order_product._ORDERS, "_launches", FakeLaunches())

    result = CliRunner().invoke(
        order_app,
        [
            "place",
            "--launch",
            "live-main",
            "--account",
            "main",
            "--symbol",
            "BTC/USDT",
            "--side",
            "buy",
            "--qty",
            "0.01",
            "--price",
            "50000",
            "--format",
            "json",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["kind"] == "order.submit"
    assert calls == [
        {
            "target": "live-main",
            "root": None,
            "launch_id": None,
            "mode": None,
            "kind": "order.submit",
            "payload": {
                "account": "main",
                "symbol": "BTC/USDT",
                "side": "buy",
                "type": "limit",
                "amount": "0.01",
                "price": "50000",
                "params": {},
            },
            "wait": False,
            "timeout_seconds": 5.0,
        }
    ]


def test_order_cancel_with_launch_uses_system_command_channel(monkeypatch) -> None:
    calls = []

    class FakeLaunches:
        def submit_command(self, **kwargs):
            calls.append(kwargs)
            return {"kind": kwargs["kind"], "command_id": "command-1"}

    monkeypatch.setattr(order_product._ORDERS, "_launches", FakeLaunches())

    result = CliRunner().invoke(
        order_app,
        ["cancel", "--launch", "live-main", "--account", "main", "--order-id", "order-1", "--format", "json"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["kind"] == "order.cancel"
    assert calls[0]["payload"]["order_id"] == "order-1"


def test_order_show_with_launch_uses_system_command_channel(monkeypatch) -> None:
    calls = []

    class FakeLaunches:
        def submit_command(self, **kwargs):
            calls.append(kwargs)
            return {"kind": kwargs["kind"], "command_id": "command-1", "response": {"status": "accepted"}}

    monkeypatch.setattr(order_product._ORDERS, "_launches", FakeLaunches())

    result = CliRunner().invoke(
        order_app,
        ["show", "--launch", "live-main", "--account", "main", "--order-id", "order-1", "--format", "json"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["kind"] == "order.status"
    assert calls[0]["wait"] is True
    assert calls[0]["payload"]["order_id"] == "order-1"


def test_order_replace_defaults_to_dry_run(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    account_root = tmp_path / ".kairos" / "accounts"
    account_root.mkdir(parents=True)
    (account_root / "binance_testnet.toml").write_text(
        "\n".join(
            [
                "[account]",
                'provider = "binance"',
                'environment = "testnet"',
                'venue = "binance"',
            ]
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        order_app,
        [
            "replace",
            "--account",
            "binance_testnet",
            "--order-id",
            "abc",
            "--symbol",
            "BTC/USDT",
            "--side",
            "buy",
            "--qty",
            "0.02",
            "--price",
            "51000",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert payload["request"]["order_id"] == "abc"
    assert payload["request"]["amount"] == "0.02"


def test_order_history_reads_closed_orders_and_writes_journal(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_workspace_manifest(tmp_path)
    account_root = tmp_path / ".kairos" / "accounts"
    account_root.mkdir(parents=True)
    (account_root / "binance_testnet.toml").write_text(
        "\n".join(
            [
                "[account]",
                'provider = "binance"',
                'environment = "testnet"',
                'venue = "binance"',
            ]
        ),
        encoding="utf-8",
    )

    class FakeBroker:
        def fetch_closed_orders(self, symbol=None, since=None, limit=None, params=None):
            return ({"id": "closed-1", "symbol": symbol, "since": since, "limit": limit},)

    monkeypatch.setattr(order_product._ORDERS, "_broker", lambda account: FakeBroker())

    result = CliRunner().invoke(
        order_app,
        [
            "history",
            "--account",
            "binance_testnet",
            "--symbol",
            "BTC/USDT",
            "--since",
            "2026-01-01T00:00:00+00:00",
            "--limit",
            "10",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["orders"][0]["id"] == "closed-1"
    journal = tmp_path / ".kairos" / "orders" / "journals" / "binance_testnet.jsonl"
    assert json.loads(journal.read_text(encoding="utf-8"))["action"] == "history"


def _write_workspace_manifest(root) -> None:
    kairos = root / ".kairos"
    kairos.mkdir(parents=True, exist_ok=True)
    (kairos / "kairos.toml").write_text("[project]\nname = \"test\"\n", encoding="utf-8")
