from __future__ import annotations

import json
from pathlib import Path
import unittest


class MassiveNotebookTests(unittest.TestCase):
    def test_notebooks_are_valid_offline_readers_with_unique_cell_ids(self):
        for name in ("massive_data_quality.ipynb", "massive_research_diagnostics.ipynb", "spxw_popular_options_2026.ipynb", "nvda_options_2026.ipynb"):
            path = Path("examples") / name
            notebook = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(notebook["nbformat"], 4)
            ids = [cell["id"] for cell in notebook["cells"]]
            self.assertEqual(len(ids), len(set(ids)))
            source = "\n".join("".join(cell.get("source", [])) for cell in notebook["cells"])
            self.assertNotIn("MASSIVE_API_KEY", source)
            self.assertNotIn("MassiveClient", source)
            self.assertNotIn("requests.get", source)
            self.assertIn("Dataset", source)
            if name == "massive_research_diagnostics.ipynb":
                self.assertIn("synthetic_forward", source)
            if name == "spxw_popular_options_2026.ipynb":
                self.assertIn("daily_representatives.parquet", source)
                self.assertIn("top_call_close", source)
                self.assertIn("atm_0dte_call_close", source)
                self.assertIn("inventory_hash", source)
            if name == "nvda_options_2026.ipynb":
                self.assertIn("features.us.massive.nvda.close-iv", source)
                self.assertIn("hexbin", source)
                self.assertIn("European approximation", source)


if __name__ == "__main__":
    unittest.main()
