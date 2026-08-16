#!/usr/bin/env python3
"""Enforce the workspace's module, contract, platform, and primitive layout."""

from __future__ import annotations

from pathlib import Path
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[2]
CRATES = ROOT / "crates"
MODULES = CRATES / "modules"
PLATFORM = CRATES / "platform"
EXPECTED_MODULES = {"account", "execution", "market", "reference", "risk"}
EXPECTED_PLATFORM = {"integration", "network", "protocol", "transport", "workspace"}
FORBIDDEN_MODULE_LAYERS = {"app", "domain", "runtime", "service"}


def manifest(path: Path) -> dict:
    return tomllib.loads(path.read_text())


def main() -> int:
    failures: list[str] = []

    actual_modules = {path.name for path in MODULES.iterdir() if path.is_dir()}
    if actual_modules != EXPECTED_MODULES:
        failures.append(
            f"module directories differ: expected {sorted(EXPECTED_MODULES)}, "
            f"found {sorted(actual_modules)}"
        )

    actual_platform = {path.name for path in PLATFORM.iterdir() if path.is_dir()}
    if actual_platform != EXPECTED_PLATFORM:
        failures.append(
            f"platform directories differ: expected {sorted(EXPECTED_PLATFORM)}, "
            f"found {sorted(actual_platform)}"
        )

    for name in sorted(EXPECTED_MODULES):
        root = MODULES / name
        main_manifest = root / "Cargo.toml"
        contract_manifest = root / "contract" / "Cargo.toml"
        if not main_manifest.is_file() or not (root / "src").is_dir():
            failures.append(f"module main crate is incomplete: {root.relative_to(ROOT)}")
            continue
        if not contract_manifest.is_file():
            failures.append(f"module contract crate is missing: {contract_manifest.relative_to(ROOT)}")
            continue

        package_name = manifest(main_manifest)["package"]["name"]
        if package_name != f"kairos-{name}":
            failures.append(f"unexpected module package name {package_name!r}: {main_manifest}")

        contract_data = manifest(contract_manifest)
        contract_name = contract_data["package"]["name"]
        if contract_name != f"kairos-{name}-contract":
            failures.append(
                f"unexpected contract package name {contract_name!r}: {contract_manifest}"
            )
        dependencies = contract_data.get("dependencies", {})
        main_packages = {f"kairos-{module}" for module in EXPECTED_MODULES}
        leaked = sorted(main_packages.intersection(dependencies))
        if leaked:
            failures.append(
                f"contract depends on main module package(s) {leaked}: {contract_manifest}"
            )

        for layer in sorted(FORBIDDEN_MODULE_LAYERS):
            if (root / layer).exists():
                failures.append(
                    f"obsolete wrapper/domain crate directory: {(root / layer).relative_to(ROOT)}"
                )

    primitives = manifest(CRATES / "primitives" / "Cargo.toml")
    if primitives["package"]["name"] != "kairos-primitives":
        failures.append("crates/primitives must provide package kairos-primitives")

    if failures:
        print("crate layout checks failed:", file=sys.stderr)
        print("\n".join(f"- {failure}" for failure in failures), file=sys.stderr)
        return 1
    print("crate layout checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
