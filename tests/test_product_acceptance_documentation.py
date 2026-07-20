from pathlib import Path
import unittest


ROOT=Path(__file__).parents[1]


class ProductAcceptanceDocumentationTests(unittest.TestCase):
    def test_all_eight_scenarios_have_existing_examples_and_tests(self):
        required=(
            "examples/studies/sma_factor_lifecycle.py","examples/backtest/governed_sma.py",
            "examples/runtime/sma_historical_simulation.py","examples/runtime/sma_paper_session.py",
            "examples/operations/manual_order.py","examples/strategy/bull_put_spread_lifecycle.py",
            "examples/lifecycle/full_product_acceptance.py","docs/product_acceptance_matrix.md",
        )
        self.assertTrue(all((ROOT/path).exists() for path in required))
        matrix=(ROOT/"docs/product_acceptance_matrix.md").read_text(encoding="utf-8")
        for number in range(1,9):self.assertIn(f"| {number} ",matrix)


if __name__=="__main__":unittest.main()
