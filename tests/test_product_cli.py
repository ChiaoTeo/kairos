from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal

from kairos.product_workflow import _write_binance_spot_bar_capture


ROOT = Path(__file__).parents[1]


def command(root: Path, *args: str) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, "-m", "kairos", "--format", "json", "--lake-root", str(root), *args],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    return json.loads(completed.stdout)


class ProductCliTests(unittest.TestCase):
    def test_product_cli_defaults_to_localized_text_and_keeps_json_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = [sys.executable, "-m", "kairos", "--lake-root", str(root)]
            chinese = subprocess.run(
                [*base, "--lang", "zh-CN", "factor", "verify-sma", "--fixture", "--fast", "5", "--slow", "15"],
                cwd=ROOT, check=True, capture_output=True, text=True,
            )
            english = subprocess.run(
                [*base, "--lang", "en-US", "factor", "verify-sma", "--fixture", "--fast", "5", "--slow", "15"],
                cwd=ROOT, check=True, capture_output=True, text=True,
            )
            machine = subprocess.run(
                [*base, "--format", "json", "factor", "verify-sma", "--fixture", "--fast", "5", "--slow", "15"],
                cwd=ROOT, check=True, capture_output=True, text=True,
            )
            quiet = subprocess.run(
                [*base, "--quiet", "factor", "verify-sma", "--fixture", "--fast", "5", "--slow", "15"],
                cwd=ROOT, check=True, capture_output=True, text=True,
            )
            failed = subprocess.run(
                [*base, "--lang", "zh-CN", "factor", "verify-sma", "--fast", "5", "--slow", "15"],
                cwd=ROOT, check=False, capture_output=True, text=True,
            )

        self.assertIn("SMA 因子验证", chinese.stdout)
        self.assertIn("批量/事件一致性", chinese.stdout)
        self.assertNotIn('{"bars"', chinese.stdout)
        self.assertIn("SMA factor validation", english.stdout)
        self.assertEqual(json.loads(machine.stdout)["bars"], 90)
        self.assertEqual(quiet.stdout, "")
        self.assertEqual(failed.returncode, 2)
        self.assertIn("命令执行失败", failed.stderr)
        self.assertIn("INPUT_DATASET_REQUIRED", failed.stderr)

    def test_sma_tutorial_and_dataset_inference_remove_governance_boilerplate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            completed = subprocess.run(
                [sys.executable, "-m", "kairos", "--format", "json", "tutorial", "sma", "--output-root", str(root)],
                cwd=ROOT, check=True, capture_output=True, text=True,
            )
            tutorial = json.loads(completed.stdout)
            repeated = subprocess.run(
                [sys.executable, "-m", "kairos", "--format", "json", "tutorial", "sma", "--output-root", str(root)],
                cwd=ROOT, check=True, capture_output=True, text=True,
            )
            created = command(root, "study", "create", "short-study", "--hypothesis", "SMA trend",
                              "--dataset", "fixture:sma-bars-v1")
            inspected = command(root, "study", "inspect", "btc-sma-first")
            preview = command(root, "study", "data", "btc-sma-first", "--head", "3", "--column", "available_time",
                              "--column", "close")
            profile = command(root, "study", "profile", "btc-sma-first")
            scaffold = command(root, "study", "scaffold", "btc-sma-first")
            scaffold_exists = Path(scaffold["script"]).exists()

        self.assertTrue(tutorial["created"])
        self.assertFalse(json.loads(repeated.stdout)["created"])
        self.assertEqual(tutorial["dataset"], "fixture:sma-bars-v1")
        self.assertEqual(len(tutorial["input_hash"]), 64)
        self.assertIn("study data", tutorial["next"])
        self.assertEqual(created["input_release"], "fixture:sma-bars-v1")
        self.assertEqual(created["input_hash"], tutorial["input_hash"])
        self.assertEqual(created["primary_time"], "available_time")
        self.assertEqual(inspected["rows"], 90)
        self.assertEqual(preview["shown"], 3)
        self.assertEqual(preview["columns"], ["available_time", "close"])
        self.assertTrue(profile["passed"])
        self.assertTrue(scaffold_exists)

    def test_builtin_multi_asset_releases_can_be_registered_inspected_and_run(self)->None:
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);registered=command(root,"strategy","register-builtins")
            inspected=command(root,"strategy","inspect","covered-call-v1","--version","1.1.0")
            status=command(root,"strategy","status","covered-call-v1","--version","1.1.0")
            active=command(root,"strategy","activate","covered-call-v1","--version","1.1.0","--actor","operator","--reason","acceptance")
            iron=command(root,"strategy","register-btc-iron-condor","--research-spec-hash","b"*64)
            iron_status=command(root,"strategy","status","btc-iron-condor-v1","--version","1.2.0")
            result=command(root,"run","reference","--strategy","covered-call")
        self.assertGreaterEqual(registered["count"],5);self.assertIn("CoveredCallStrategy",inspected["implementation"]["import_path"])
        self.assertTrue(status["complete"]);self.assertEqual(active["active_version"],"1.1.0")
        self.assertEqual(iron["version"],"1.2.0");self.assertTrue(iron_status["complete"])
        self.assertTrue(result["replay_equal"]);self.assertTrue(result["stress_is_worse"])

    def test_scenarios_one_through_four_run_from_product_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            created = command(root, "study", "create", "btc-sma", "--hypothesis", "SMA trend",
                "--input-release", "fixture:sma-bars-v1", "--input-hash", "a"*64,
                "--start", "2025-01-01T00:00:00Z", "--end", "2026-01-01T00:00:00Z")
            frozen = command(root, "study", "freeze", "btc-sma")
            factor = command(root, "factor", "register-sma", "--input-identity", "fixture:sma-bars-v1",
                "--fast", "5", "--slow", "15")
            verified = command(root, "factor", "verify-sma", "--fixture", "--fast", "5", "--slow", "15")
            strategy = command(root, "strategy", "register-sma", "--input-identity", "fixture:sma-bars-v1",
                "--fast", "5", "--slow", "15")
            generic_backtest = command(root, "run", "backtest", "--strategy", "sma-cross-v1@1.2.0",
                "--fixture", "--fast", "5", "--slow", "15")
            backtest = command(root, "run", "backtest-sma", "--fixture", "--fast", "5", "--slow", "15")
            run_root = root/"runs"/"sma"
            simulation = command(root, "run", "simulate-sma", "--fixture", "--fast", "5", "--slow", "15",
                "--run-root", str(run_root))
            generic_simulation = command(root, "run", "simulate", "--strategy", "sma-cross-v1@1.2.0",
                "--fixture", "--fast", "5", "--slow", "15", "--run-root", str(root/"runs"/"sma-generic"))
            high_fee_simulation = command(root, "run", "simulate-sma", "--fixture", "--fast", "5", "--slow", "15",
                "--fee-bps", "25", "--run-root", str(root/"runs"/"sma-high-fee"))
            calibration = command(root, "runtime", "calibrate-execution",
                "--db", high_fee_simulation["runtime_database"], "--output-root", str(root/"calibration"),
                "--venue", "simulated", "--environment", "testnet", "--strategy", "sma-cross-v1")
            calibrated_backtest = command(root, "run", "backtest", "--strategy", "sma-cross-v1@1.2.0",
                "--fixture", "--fast", "5", "--slow", "15", "--execution-calibration", calibration["manifest"])
            calibrated_artifact = json.loads(Path(calibrated_backtest["artifact"]).read_text())
            inspected = command(root, "run", "inspect", "--db", simulation["runtime_database"])
            explained = command(root, "run", "inspect", "--artifact", simulation["artifact"],
                "--at", "2026-01-02T00:00:00Z")
            replayed = command(root, "run", "replay-sma", "--artifact", simulation["artifact"], "--fixture")
            paper=command(root,"run","paper-sma","--fixture","--fast","5","--slow","15",
                "--run-root",str(root/"paper-runtime"),"--artifact-root",str(root/"paper-artifacts"))
            generic_paper=command(root,"run","paper","--strategy","sma-cross-v1@1.2.0","--fixture","--fast","5","--slow","15",
                "--run-root",str(root/"paper-runtime-generic"),"--artifact-root",str(root/"paper-artifacts-generic"))
            paper_replay=command(root,"run","replay-sma-capture","--artifact",paper["artifact"],"--capture",paper["capture"])
            shadow=command(root,"run","shadow-sma","--capture",paper["capture"],"--fast","5","--slow","15",
                "--run-root",str(root/"shadow-runtime"),"--artifact-root",str(root/"shadow-artifacts"))
            generic_shadow=command(root,"run","shadow","--strategy","sma-cross-v1@1.2.0","--capture",paper["capture"],"--fast","5","--slow","15",
                "--run-root",str(root/"shadow-runtime-generic"),"--artifact-root",str(root/"shadow-artifacts-generic"))
            shadow_replay=command(root,"run","replay-sma-capture","--artifact",shadow["artifact"],"--capture",shadow["capture"])
            unsupported = subprocess.run(
                [sys.executable, "-m", "kairos", "--format", "json", "--lake-root", str(root),
                 "run", "shadow", "--strategy", "covered-call-v1@1.1.0", "--fixture",
                 "--run-root", str(root/"unsupported-shadow")],
                cwd=ROOT, check=False, capture_output=True, text=True,
            )

        self.assertEqual(created["status"], "sandbox")
        self.assertEqual(frozen["status"], "frozen_candidate")
        self.assertEqual(len(factor["factor_spec_hash"]), 64)
        self.assertTrue(verified["batch_replay_equal"])
        self.assertEqual(strategy["factor_spec_hash"], factor["factor_spec_hash"])
        self.assertEqual(generic_backtest["audit_hash"], backtest["audit_hash"])
        self.assertEqual(calibrated_backtest["execution_calibration"]["status"], "bound")
        self.assertEqual(calibrated_backtest["execution_calibration"]["release_hash"], calibration["release_hash"])
        self.assertEqual(calibrated_backtest["execution_calibration"]["sample_count"], calibration["sample_count"])
        self.assertEqual(calibrated_artifact["execution"]["calibration"]["release_hash"], calibration["release_hash"])
        self.assertEqual(calibrated_artifact["execution"]["fill_model"], "next-open-bar")
        self.assertEqual(calibrated_artifact["execution"]["calibrated_result"]["status"], "applied")
        self.assertEqual(Decimal(calibrated_artifact["execution"]["calibrated_result"]["calibrated_fee_bps"]), Decimal("25"))
        self.assertLess(Decimal(calibrated_backtest["calibrated_final_equity"]), Decimal(backtest["final_equity"]))
        for name in ("factor_hash", "decision_hash", "intent_hash"):
            self.assertEqual(backtest[name], simulation[name])
            self.assertEqual(simulation[name], generic_simulation[name])
        self.assertTrue(simulation["restart_ready"])
        self.assertTrue(generic_simulation["restart_ready"])
        self.assertGreater(inspected["transactions"], 1)
        self.assertIsNotNone(explained["factor"]); self.assertIsNotNone(explained["decision"])
        self.assertIsNotNone(explained["attribution"])
        self.assertTrue(replayed["passed"])
        self.assertEqual(paper["mode"],"paper-trading");self.assertTrue(paper["restart_ready"])
        self.assertEqual(generic_paper["mode"],"paper-trading");self.assertTrue(generic_paper["restart_ready"])
        self.assertEqual(generic_paper["factor_hash"],paper["factor_hash"])
        self.assertEqual(generic_paper["decision_hash"],paper["decision_hash"])
        self.assertEqual(generic_paper["intent_hash"],paper["intent_hash"])
        self.assertTrue(paper_replay["passed"])
        self.assertEqual(shadow["mode"],"shadow")
        self.assertEqual(generic_shadow["mode"],"shadow")
        self.assertEqual(shadow["orders"],0);self.assertEqual(shadow["fills"],0)
        self.assertEqual(shadow["submitted_orders"],0);self.assertGreater(shadow["hypothetical_intents"],0)
        self.assertEqual(shadow["factor_hash"],paper["factor_hash"])
        self.assertEqual(shadow["decision_hash"],paper["decision_hash"])
        self.assertEqual(shadow["intent_hash"],paper["intent_hash"])
        self.assertEqual(generic_shadow["factor_hash"],shadow["factor_hash"])
        self.assertEqual(generic_shadow["decision_hash"],shadow["decision_hash"])
        self.assertEqual(generic_shadow["intent_hash"],shadow["intent_hash"])
        self.assertTrue(shadow_replay["passed"])
        self.assertEqual(unsupported.returncode, 2)
        self.assertIn("currently supports sma-cross-v1", unsupported.stderr)

    def test_strategy_promotion_cli_records_gate_checked_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command(root, "strategy", "register-sma", "--input-identity", "fixture:sma-bars-v1",
                "--fast", "5", "--slow", "15")
            evidence = root / "research-result.json"
            evidence.write_text(json.dumps({"state": {"maximum_level": 2, "signal_status": "SUPPORTED"}}))
            checked = command(root, "strategy", "check-promotion", "sma-cross-v1", "--version", "1.2.0",
                "--to", "RESEARCH_VALIDATED", "--evidence", str(evidence))
            before = command(root, "strategy", "status", "sma-cross-v1", "--version", "1.2.0")
            promoted = command(root, "strategy", "promote", "sma-cross-v1", "--version", "1.2.0",
                "--to", "RESEARCH_VALIDATED", "--evidence", str(evidence), "--actor", "reviewer",
                "--capital-limit", "10000", "--rollback-condition", "signal evidence invalidated")
            status = command(root, "strategy", "status", "sma-cross-v1", "--version", "1.2.0")
            bundle = json.loads(Path(promoted["evidence_bundle"]).read_text())

        self.assertTrue(checked["gate_passed"])
        self.assertTrue(checked["transition_valid"])
        self.assertTrue(checked["would_promote"])
        self.assertEqual(before["lifecycle"], "DRAFT")
        self.assertTrue(promoted["gate_passed"])
        self.assertEqual(promoted["status"], "RESEARCH_VALIDATED")
        self.assertEqual(bundle["kind"], "strategy_promotion_evidence_bundle")
        self.assertEqual(bundle["to"], "RESEARCH_VALIDATED")
        self.assertEqual(status["latest_promotion_bundle"], promoted["evidence_bundle"])
        self.assertEqual(status["lifecycle"], "RESEARCH_VALIDATED")

    def test_strategy_check_promotion_reports_external_gate_failure_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command(root, "strategy", "register-sma", "--input-identity", "fixture:sma-bars-v1",
                "--fast", "5", "--slow", "15")
            fixture_l5 = root / "fixture-l5.json"
            fixture_l5.write_text(json.dumps({
                "state": {"maximum_level": 5, "strategy_status": "SUPPORTED"},
                "out_of_sample": "decision_oos",
                "evidence_scope": "local_acceptance",
            }))
            checked = command(root, "strategy", "check-promotion", "sma-cross-v1", "--version", "1.2.0",
                "--to", "PAPER_APPROVED", "--evidence", str(fixture_l5))
            status = command(root, "strategy", "status", "sma-cross-v1", "--version", "1.2.0")

        self.assertFalse(checked["gate_passed"])
        self.assertFalse(checked["would_promote"])
        self.assertIn("paper approval requires decision-OOS L5 robustness evidence", checked["gate_reasons"])
        self.assertEqual(status["lifecycle"], "DRAFT")

    def test_strategy_check_promotion_blocks_lifecycle_skip_even_when_gate_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command(root, "strategy", "register-sma", "--input-identity", "fixture:sma-bars-v1",
                "--fast", "5", "--slow", "15")
            evidence = root / "trade-proxy-result.json"
            evidence.write_text(json.dumps({"state": {"maximum_level": 3, "strategy_status": "SUPPORTED"}}))
            checked = command(root, "strategy", "check-promotion", "sma-cross-v1", "--version", "1.2.0",
                "--to", "TRADE_PROXY_VALIDATED", "--evidence", str(evidence))
            failed = subprocess.run(
                [sys.executable, "-m", "kairos", "--format", "json", "--lake-root", str(root),
                 "strategy", "promote", "sma-cross-v1", "--version", "1.2.0",
                 "--to", "TRADE_PROXY_VALIDATED", "--evidence", str(evidence), "--actor", "reviewer",
                 "--capital-limit", "10000", "--rollback-condition", "proxy evidence invalidated"],
                cwd=ROOT, check=False, capture_output=True, text=True,
            )
            status = command(root, "strategy", "status", "sma-cross-v1", "--version", "1.2.0")

        self.assertTrue(checked["gate_passed"])
        self.assertFalse(checked["transition_valid"])
        self.assertIn("invalid strategy promotion", checked["transition_reason"])
        self.assertFalse(checked["would_promote"])
        self.assertEqual(failed.returncode, 2)
        self.assertIn("strategy promotion transition failed", failed.stderr)
        self.assertEqual(status["lifecycle"], "DRAFT")

    def test_public_binance_bar_capture_can_drive_strategy_paper(self) -> None:
        class Transport:
            def request(self, method, path, params=None, headers=None):
                self.call = (method, path, params, headers)
                start = 1_735_689_600_000
                rows = []
                for index in range(20):
                    open_time = start + index * 60_000
                    close = Decimal("100") + Decimal(index)
                    rows.append([
                        open_time, str(close - Decimal("1")), str(close + Decimal("1")),
                        str(close - Decimal("2")), str(close), "1.5", open_time + 59_999,
                    ])
                return rows

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capture = root / "live" / "btc.canonical.jsonl"
            written = _write_binance_spot_bar_capture(
                capture, symbol="BTCUSDT", interval="1m", limit=20,
                base_url="https://example.invalid", transport=Transport(),
            )
            paper = command(root, "run", "paper", "--strategy", "sma-cross-v1@1.2.0",
                "--capture", str(capture), "--fast", "3", "--slow", "5",
                "--run-root", str(root/"paper-runtime"), "--artifact-root", str(root/"paper-artifacts"))
            replay = command(root, "run", "replay-sma-capture",
                "--artifact", paper["artifact"], "--capture", paper["capture"])
            artifact_exists = Path(paper["artifact"]).exists()

        self.assertEqual(written["bars"], 20)
        self.assertEqual(written["symbol"], "BTCUSDT")
        self.assertEqual(paper["mode"], "paper-trading")
        self.assertEqual(paper["bars"], 20)
        self.assertTrue(paper["input_identity"].startswith("capture:"))
        self.assertTrue(artifact_exists)
        self.assertTrue(replay["passed"])
        self.assertTrue(replay["comparisons"]["strategy_run_audit_hash"])


if __name__ == "__main__":
    unittest.main()
