import unittest
from unittest.mock import patch

from kairospy.surface.cli.output import render_product_result, resolve_language


class CliLiveOutputTests(unittest.TestCase):
    def test_resolve_language_accepts_kairospy_lang_override(self) -> None:
        with patch.dict("os.environ", {"KAIROSPY_LANG": "zh-CN", "LC_ALL": "C.UTF-8"}, clear=True):
            self.assertEqual(resolve_language(None), "zh-CN")

    def test_run_config_validate_zh_output_is_table_oriented(self) -> None:
        payload = {
            "product": "run",
            "operation": "config.validate",
            "path": "/tmp/position-lab-live-print.toml",
            "valid": True,
            "issues": [],
            "run": {
                "name": "position-lab-live-print",
                "mode": "live",
                "workspace": "examples.workspace.position_lab:build_workspace",
                "strategy": "examples.strategies.printer:build",
            },
            "params": {"workspace_profile": "position-lab", "instrument": "BTC"},
            "bindings": {
                "account": "binance_live_spot",
                "market": ["hl_orderbook", "hl_funding", "binance_spot_book"],
                "execution": "manual-target-position",
            },
            "guards": {
                "confirm_live_required": True,
                "require_readiness": False,
                "start_reduce_only": True,
            },
            "strategy": {},
            "live": {
                "provider": "binance",
                "execution_driver": "manual-target-position",
                "bind_provider": False,
                "bind_ports": False,
            },
        }

        rendered = render_product_result("run", "config", payload, "zh-CN")

        self.assertIn("运行配置检查", rendered)
        self.assertIn("配置摘要", rendered)
        self.assertIn("资源绑定", rendered)
        self.assertIn("安全开关", rendered)
        self.assertIn("可启动", rendered)
        self.assertIn("✓ 是", rendered)
        self.assertIn("binance_spot_book", rendered)
        self.assertIn("启动: kairospy run live start", rendered)
        self.assertIn("控制台: kairospy run live attach", rendered)
        self.assertNotIn("--foreground", rendered)
        self.assertNotIn("name=position-lab-live-print", rendered)
        self.assertNotIn("confirm_live_required=✓", rendered)

    def test_run_live_status_human_output_hides_internal_snapshot_details(self) -> None:
        payload = {
            "product": "run",
            "operation": "live",
            "live_action": "status",
            "run_id": "position-lab-live",
            "status": "stale",
            "phase": "stopped",
            "application_status": "stopped",
            "reason": "stopped",
            "stop_requested": False,
            "snapshot_hash": "8c62c7fa5a33993a149232667437aac7532d220e195a93bb903d58dfbad2183e",
            "runtime_database": "/tmp/runtime.sqlite3",
            "state_key": "live_run_daemon:position-lab-live",
            "structured_log_file": "/tmp/runtime.jsonl",
            "services": (
                {"name": "strategy-run:position-lab-live", "status": "stopped", "restart_count": 0},
            ),
            "operator_commands": (
                {
                    "command_id": "operator:abc",
                    "run_id": "position-lab-live",
                    "command_type": "target_position",
                    "payload": {"intent_id": "manual-pair-001"},
                    "status": "succeeded",
                    "idempotency_key": "target_position:hash",
                },
                {
                    "command_id": "operator:def",
                    "run_id": "position-lab-live",
                    "command_type": "request_status_snapshot",
                    "status": "pending",
                    "idempotency_key": "request_status_snapshot:hash",
                },
            ),
            "target_position": {
                "status": "accepted",
                "intent_id": "manual-pair-001",
                "execution_status": "not_submitted",
                "legs": (
                    {"venue": "binance", "product": "spot", "instrument": "BTCUSDT", "side": "long", "quantity": "0.001"},
                    {"venue": "hyperliquid", "product": "perpetual", "instrument": "BTC", "side": "short", "quantity": "0.001"},
                ),
                "updated_at": "2026-07-23T01:32:57.716936+00:00",
            },
            "health": {"status": "stale", "healthy": False, "reasons": ("runtime_stale", "heartbeat_stale")},
            "metrics": {
                "operator_command_backlog": 1,
                "unresolved_order_count": 0,
                "outbox_backlog_count": 0,
                "market_freshness_status": "unknown",
                "open_incident_count": 1,
            },
            "incidents": (
                {"severity": "warning", "status": "open", "title": "runtime health stale"},
            ),
            "open_incident_count": 1,
        }

        rendered = render_product_result("run", "live", payload, "en-US")

        self.assertIn("Live Run Status", rendered)
        self.assertIn("position-lab-live", rendered)
        self.assertIn("stale", rendered)
        self.assertIn("manual-pair-001", rendered)
        self.assertIn("long 0.001 BTCUSDT @ binance", rendered)
        self.assertIn("Pending commands", rendered)
        self.assertNotIn("runtime.sqlite3", rendered)
        self.assertNotIn("snapshot_hash", rendered)
        self.assertNotIn("operator_commands", rendered)
        self.assertNotIn("idempotency_key", rendered)
        self.assertNotIn("8c62c7fa", rendered)

    def test_run_live_control_command_human_output_is_operator_focused(self) -> None:
        payload = {
            "product": "run",
            "operation": "live",
            "live_action": "target-position",
            "run_id": "position-lab-live",
            "status": "command_submitted",
            "runtime_database": "/tmp/runtime.sqlite3",
            "operator_command": {
                "command_id": "operator:abc",
                "run_id": "position-lab-live",
                "command_type": "target_position",
                "status": "pending",
                "idempotency_key": "target_position:hash",
                "reason": "manual pair position target",
            },
        }

        rendered = render_product_result("run", "live", payload, "en-US")

        self.assertIn("Live Run Command", rendered)
        self.assertIn("target position", rendered)
        self.assertIn("pending", rendered)
        self.assertNotIn("operator:abc", rendered)
        self.assertNotIn("runtime.sqlite3", rendered)
        self.assertNotIn("idempotency_key", rendered)


if __name__ == "__main__":
    unittest.main()
