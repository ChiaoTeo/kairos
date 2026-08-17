from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
JSON_MODEL_ADAPTER = re.compile(
    r"serde_json::(?:to_value|from_value|to_string|Value)"
)


def test_business_publishers_do_not_use_json_as_a_model_adapter() -> None:
    for module in ("account", "execution", "market", "risk"):
        composition = (
            ROOT
            / "crates"
            / "modules"
            / module
            / "src"
            / "composition"
            / "mod.rs"
        )
        assert not JSON_MODEL_ADAPTER.search(composition.read_text()), composition
