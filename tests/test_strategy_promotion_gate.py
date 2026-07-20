import unittest
from hashlib import sha256
import json

from kairospy.domain.strategy_contract import StrategyLifecycle
from kairospy.strategies.promotion import evaluate_promotion_artifacts


class StrategyPromotionGateTest(unittest.TestCase):
    def test_l3_proxy_cannot_promote_to_live_but_supported_signal_can_promote_study(self):
        proxy={"state":{"maximum_level":3,"signal_status":"EXPLORATORY","strategy_status":"TRADE_PROXY_ONLY"},"out_of_sample":"time_oos"}
        self.assertFalse(evaluate_promotion_artifacts(StrategyLifecycle.LIVE_LIMITED,(proxy,)).passed)
        signal={"state":{"maximum_level":2,"signal_status":"SUPPORTED"}}
        self.assertTrue(evaluate_promotion_artifacts(StrategyLifecycle.STUDY_VALIDATED,(signal,)).passed)

    def test_fixture_l5_cannot_promote_to_paper(self):
        fixture_l5={
            "state":{"maximum_level":5,"strategy_status":"SUPPORTED"},
            "out_of_sample":"decision_oos","evidence_scope":"local_acceptance",
        }
        decision=evaluate_promotion_artifacts(StrategyLifecycle.PAPER_APPROVED,(fixture_l5,))
        self.assertFalse(decision.passed)
        self.assertIn("paper approval requires explicit Paper/Testnet readiness evidence; local fixture evidence is not enough",
            decision.reasons)

    def test_paper_approval_requires_l5_and_external_readiness(self):
        robust={
            "state":{"maximum_level":5,"strategy_status":"SUPPORTED"},
            "out_of_sample":"decision_oos","environment":"testnet",
        }
        readiness={
            "kind":"runtime_l4_preflight","ready":True,"venue":"binance","environment":"testnet",
            "checks":{
                "environment_compatible":True,"external_connection_ready":True,
                "instrument_listing_ready":True,
            },
        }
        readiness=_audited(readiness)
        self.assertTrue(evaluate_promotion_artifacts(StrategyLifecycle.PAPER_APPROVED,(robust,readiness)).passed)
        tampered={**readiness,"ready":False}
        self.assertFalse(evaluate_promotion_artifacts(StrategyLifecycle.PAPER_APPROVED,(robust,tampered)).passed)

    def test_live_promotion_requires_passed_external_soak(self):
        local_l6={"state":{"maximum_level":6},"environment":"simulated","local_only":True}
        self.assertFalse(evaluate_promotion_artifacts(StrategyLifecycle.LIVE_LIMITED,(local_l6,)).passed)
        soak={
            "kind":"runtime_l4_soak","passed":True,"environment":"testnet",
            "acceptance":{
                "duration_met":True,"all_cycles_healthy":True,"no_critical_alerts":True,
                "restart_drill_passed":True,"kill_switch_drill_passed":True,
            },
        }
        soak=_audited(soak)
        self.assertTrue(evaluate_promotion_artifacts(StrategyLifecycle.LIVE_LIMITED,(soak,)).passed)
        tampered={**soak,"acceptance":{**soak["acceptance"],"no_critical_alerts":False}}
        self.assertFalse(evaluate_promotion_artifacts(StrategyLifecycle.LIVE_LIMITED,(tampered,)).passed)


def _audited(payload: dict) -> dict:
    material = {key: value for key, value in payload.items() if key not in {"artifact", "audit_hash"}}
    audit_hash = sha256(json.dumps(material, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {**payload, "audit_hash": audit_hash}


if __name__=="__main__":unittest.main()
