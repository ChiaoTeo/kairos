#!/usr/bin/env python3
"""Enforce the workspace's module, contract, platform, and primitive layout."""

from __future__ import annotations

from pathlib import Path
import re
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[2]
CRATES = ROOT / "crates"
MODULES = CRATES / "modules"
PLATFORM = CRATES / "platform"
SYSTEM = CRATES / "system"
FORBIDDEN_MODULE_LAYERS = {"app", "domain", "runtime", "service"}
IGNORED_PLATFORM_DIRECTORIES = {"legacy"}


def manifest(path: Path) -> dict:
    return tomllib.loads(path.read_text())


def dependency_names(data: dict) -> set[str]:
    names: set[str] = set()
    for section in ("dependencies", "dev-dependencies", "build-dependencies"):
        names.update(data.get(section, {}))
    for target in data.get("target", {}).values():
        for section in ("dependencies", "dev-dependencies", "build-dependencies"):
            names.update(target.get(section, {}))
    return names


def main() -> int:
    failures: list[str] = []

    module_names = {path.name for path in MODULES.iterdir() if path.is_dir()}
    platform_names = {
        path.name
        for path in PLATFORM.iterdir()
        if path.is_dir() and path.name not in IGNORED_PLATFORM_DIRECTORIES
    }
    system_names = {path.name for path in SYSTEM.iterdir() if path.is_dir()}
    if not module_names:
        failures.append("crates/modules contains no business modules")
    if not platform_names:
        failures.append("crates/platform contains no platform crates")
    if not system_names:
        failures.append("crates/system contains no system composition crates")

    main_packages = {f"kairos-{module}" for module in module_names}
    contract_packages = {f"kairos-{module}-contract" for module in module_names}

    for name in sorted(module_names):
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
        main_dependencies = dependency_names(manifest(main_manifest))
        leaked_main_packages = sorted((main_packages - {package_name}) & main_dependencies)
        if leaked_main_packages:
            failures.append(
                f"business main crate depends on other business main package(s) "
                f"{leaked_main_packages}: {main_manifest}"
            )

        contract_data = manifest(contract_manifest)
        contract_name = contract_data["package"]["name"]
        if contract_name != f"kairos-{name}-contract":
            failures.append(
                f"unexpected contract package name {contract_name!r}: {contract_manifest}"
            )
        dependencies = dependency_names(contract_data)
        leaked = sorted(main_packages.intersection(dependencies))
        if leaked:
            failures.append(
                f"contract depends on main module package(s) {leaked}: {contract_manifest}"
            )

        for source_path in sorted((root / "contract" / "src").rglob("*.rs")):
            source = source_path.read_text(encoding="utf-8")
            for match in re.finditer(
                r"(?m)^\s*pub\s+[a-zA-Z_][a-zA-Z0-9_]*\s*:\s*(?:usize|isize)\b",
                source,
            ):
                line = source.count("\n", 0, match.start()) + 1
                failures.append(
                    f"contract DTO field must use a fixed-width numeric type: "
                    f"{source_path.relative_to(ROOT)}:{line}"
                )

        for layer in sorted(FORBIDDEN_MODULE_LAYERS):
            if (root / layer).exists():
                failures.append(
                    f"obsolete wrapper/domain crate directory: {(root / layer).relative_to(ROOT)}"
                )

        legacy_conflux = root / "src" / "application" / "conflux.rs"
        if legacy_conflux.exists():
            failures.append(
                "reusable process adapter must live under application/process: "
                f"{legacy_conflux.relative_to(ROOT)}"
            )

    for name in sorted(platform_names):
        platform_manifest = PLATFORM / name / "Cargo.toml"
        if not platform_manifest.is_file():
            failures.append(
                "platform crate manifest is missing: "
                f"{platform_manifest.relative_to(ROOT)}"
            )
            continue
        leaked_business_packages = sorted(
            (main_packages | contract_packages) & dependency_names(manifest(platform_manifest))
        )
        if leaked_business_packages:
            failures.append(
                f"platform crate depends on business package(s) {leaked_business_packages}: "
                f"{platform_manifest}"
            )

    for name in sorted(system_names):
        system_manifest = SYSTEM / name / "Cargo.toml"
        if not system_manifest.is_file():
            failures.append(
                "system crate manifest is missing: " f"{system_manifest.relative_to(ROOT)}"
            )
            continue
        leaked_main_packages = sorted(
            main_packages & dependency_names(manifest(system_manifest))
        )
        if leaked_main_packages:
            failures.append(
                f"system composition depends on business main package(s) "
                f"{leaked_main_packages}: {system_manifest}"
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
