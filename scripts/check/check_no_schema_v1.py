"""Reject retired Kairos wire-schema v1 artifacts and references."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RETIRED_VERSION = "v" + "1"
RETIRED_MODULE = "v_" + "1"
PATH_ROOTS = (
    ROOT / "schemas",
    ROOT / "crates" / "platform" / "protocol" / "src" / "generated",
    ROOT / "kairospy" / "infrastructure" / "transport" / "generated",
)
TEXT_ROOTS = (
    ROOT / "scripts" / "generate",
    ROOT / "crates" / "platform" / "protocol",
    ROOT / "kairospy" / "infrastructure" / "transport",
    ROOT / "tests" / "fixtures",
)
FORBIDDEN_IDENTIFIERS = (
    "schema_root/" + RETIRED_VERSION,
    "schemas/" + RETIRED_VERSION,
    *(f"kairos.{owner}.{RETIRED_VERSION}" for owner in (
        "market", "execution", "account", "risk", "common", "intent", "system"
    )),
    RETIRED_MODULE + "::",
    *(identifier + "1" for identifier in (
        "MQT", "MTR", "MBA", "MGR", "PMC", "EXE", "RKE"
    )),
)


def main() -> int:
    failures: list[str] = []
    for root in PATH_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if any(part in {RETIRED_VERSION, RETIRED_MODULE} for part in path.parts):
                failures.append(f"retired schema-v1 path: {path.relative_to(ROOT)}")

    for root in TEXT_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text()
            except UnicodeDecodeError:
                continue
            for identifier in FORBIDDEN_IDENTIFIERS:
                if identifier in text:
                    failures.append(
                        f"retired schema-v1 reference {identifier!r}: "
                        f"{path.relative_to(ROOT)}"
                    )

    if failures:
        print("retired Kairos schema v1 references found:")
        print("\n".join(f"- {failure}" for failure in sorted(set(failures))))
        return 1
    print("no active Kairos schema v1 artifacts or references found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
