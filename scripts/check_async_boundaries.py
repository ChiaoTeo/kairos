"""Reject blocking HTTP clients from Tokio-owned application code.

Provider adapters may still use the synchronous facade, but it must live below
the Integration boundary. This check prevents an accidental direct blocking
client from being added to a business process handler.
"""

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
APPLICATION_ROOTS = tuple((ROOT / "crates/business").glob("*/service/src/application"))


def main() -> int:
    violations: list[str] = []
    for root in APPLICATION_ROOTS:
        for path in root.rglob("*.rs"):
            text = path.read_text(encoding="utf-8")
            if "reqwest::blocking" in text or "use reqwest::{blocking" in text:
                if re.search(r"\basync\s+fn\b", text):
                    violations.append(str(path.relative_to(ROOT)))
    if violations:
        print("blocking HTTP client found in async application code:")
        print("\n".join(sorted(violations)))
        return 1
    print("async HTTP boundary check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
