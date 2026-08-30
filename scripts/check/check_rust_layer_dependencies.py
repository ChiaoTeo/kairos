#!/usr/bin/env python3
"""Enforce dependency direction across every business main crate."""

from __future__ import annotations

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[2]
MODULES = ROOT / "crates" / "modules"
BUSINESS_MODULES = tuple(
    path.name
    for path in sorted(MODULES.iterdir())
    if path.is_dir() and (path / "Cargo.toml").is_file()
)

FORBIDDEN_IMPORTS = {
    "domain": ("application", "composition", "services"),
    "services": ("application", "composition"),
    "application": ("composition",),
}


def production_source(path: Path) -> str:
    """Return the production prefix, excluding conventional test-only tails."""
    if path.name == "tests.rs" or "tests" in path.parts:
        return ""
    source = path.read_text(encoding="utf-8")
    # Only a conventional trailing test module can delimit production code.
    # Individual cfg(test) helpers are often interleaved with later production
    # items and must not hide the remainder of the file from this check.
    test_tail = re.search(
        r"(?m)^\s*#\[cfg\(test\)\]\s*\n\s*mod\s+tests\s*(?:;|\{)", source
    )
    return source[: test_tail.start()] if test_tail else source


def main() -> int:
    failures: list[str] = []
    for module in BUSINESS_MODULES:
        source_root = MODULES / module / "src"
        if not source_root.is_dir():
            failures.append(f"missing module source root: {source_root.relative_to(ROOT)}")
            continue
        for layer, forbidden_layers in FORBIDDEN_IMPORTS.items():
            layer_root = source_root / layer
            if not layer_root.is_dir():
                failures.append(f"missing standard layer: {layer_root.relative_to(ROOT)}")
                continue
            for path in sorted(layer_root.rglob("*.rs")):
                source = production_source(path)
                if layer == "domain":
                    for match in re.finditer(r"\bkairos_(?!primitives\b)[a-zA-Z0-9_]+", source):
                        line = source.count("\n", 0, match.start()) + 1
                        failures.append(
                            f"{path.relative_to(ROOT)}:{line}: domain may depend only on "
                            "kairos_primitives, not another Kairos crate"
                        )
                    raw_identity = re.compile(
                        r"(?m)^\s*pub(?:\(crate\))?\s+"
                        r"(?:account_id|market_id|instrument_id|order_id|intent_id|plan_id|"
                        r"strategy_id|execution_route_id|remote_order_id|reservation_id|"
                        r"policy_id|actor_id|source_id|launch_id|instance_id)"
                        r"\s*:\s*(?:Option<)?String\b"
                    )
                    raw_sequence = re.compile(
                        r"(?m)^\s*pub(?:\(crate\))?\s+[a-zA-Z_][a-zA-Z0-9_]*"
                        r"(?:sequence|generation|_unix_nanos|revision)[a-zA-Z0-9_]*"
                        r"\s*:\s*(?:Option<)?u64\b"
                    )
                    for label, pattern in (
                        ("raw identity String", raw_identity),
                        ("raw sequence/time u64", raw_sequence),
                    ):
                        for match in pattern.finditer(source):
                            line = source.count("\n", 0, match.start()) + 1
                            failures.append(
                                f"{path.relative_to(ROOT)}:{line}: domain exposes {label}; "
                                "use an owned semantic type"
                            )
                for forbidden in forbidden_layers:
                    pattern = re.compile(
                        rf"\b(?:crate|super(?:::\s*super)*)\s*::\s*{forbidden}\b"
                    )
                    for match in pattern.finditer(source):
                        line = source.count("\n", 0, match.start()) + 1
                        failures.append(
                            f"{path.relative_to(ROOT)}:{line}: "
                            f"{layer} must not depend on {forbidden}"
                        )

        application_root = source_root / "application"
        for path in sorted(application_root.rglob("*.rs")):
            if "process" in path.relative_to(application_root).parts:
                continue
            source = production_source(path)
            for match in re.finditer(r"\bkairos_conflux\b", source):
                line = source.count("\n", 0, match.start()) + 1
                failures.append(
                    f"{path.relative_to(ROOT)}:{line}: application may use "
                    "kairos_conflux only inside its reusable process facade"
                )

        root_source = (source_root / "lib.rs").read_text(encoding="utf-8")
        if re.search(r"(?m)^\s*pub\s+mod\s+domain\s*;", root_source):
            failures.append(
                f"{(source_root / 'lib.rs').relative_to(ROOT)}: domain is an internal "
                "business core; expose use-case types through application"
            )

    if failures:
        print("Rust layer dependency checks failed:", file=sys.stderr)
        print("\n".join(f"- {failure}" for failure in failures), file=sys.stderr)
        return 1
    checked = ", ".join(BUSINESS_MODULES)
    print(f"Rust layer dependency checks passed ({checked})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
