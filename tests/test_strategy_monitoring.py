from decimal import Decimal
import unittest

from kairos.orchestration.strategy_monitoring import (
    StrategyHealth,StrategyHealthMonitor,StrategyMonitoringLimits,StrategyMonitoringSnapshot,
)


class StrategyMonitoringTest(unittest.TestCase):
    def test_reconciliation_or_drawdown_suspends_while_execution_drift_degrades(self):
        limits=StrategyMonitoringLimits(Decimal(".10"),Decimal("20"),Decimal(".5"),Decimal(".2"),60)
        monitor=StrategyHealthMonitor()
        degraded=monitor.evaluate(StrategyMonitoringSnapshot("s",Decimal("-.02"),Decimal("25"),Decimal(".8"),Decimal(".1"),1),limits)
        self.assertEqual(degraded.health,StrategyHealth.DEGRADED);self.assertEqual(degraded.capital_multiplier,Decimal(".5"))
        stopped=monitor.evaluate(StrategyMonitoringSnapshot("s",Decimal("-.11"),Decimal("1"),Decimal(".8"),Decimal(".1"),1),limits)
        self.assertEqual(stopped.health,StrategyHealth.SUSPEND);self.assertEqual(stopped.capital_multiplier,0)


if __name__=="__main__":unittest.main()
