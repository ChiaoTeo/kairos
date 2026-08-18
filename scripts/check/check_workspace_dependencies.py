#!/usr/bin/env python3
"""Enforce centralized Cargo dependency and package metadata declarations."""

from __future__ import annotations

from pathlib import Path
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[2]
ROOT_MANIFEST = ROOT / "Cargo.toml"
PACKAGE_FIELDS = ("version", "edition", "license")
DEPENDENCY_SECTIONS = ("dependencies", "dev-dependencies", "build-dependencies")
MEMBER_OWNED_DEPENDENCY_KEYS = {"features", "optional", "workspace"}


def load_manifest(path: Path) -> dict:
    return tomllib.loads(path.read_text())


def dependency_tables(manifest: dict) -> list[tuple[str, dict]]:
    tables: list[tuple[str, dict]] = []
    for section in DEPENDENCY_SECTIONS:
        tables.append((section, manifest.get(section, {})))
    for target, target_config in manifest.get("target", {}).items():
        for section in DEPENDENCY_SECTIONS:
            tables.append(
                (f"target.{target}.{section}", target_config.get(section, {}))
            )
    return tables


def main() -> int:
    failures: list[str] = []
    root = load_manifest(ROOT_MANIFEST)
    workspace = root.get("workspace", {})
    workspace_package = workspace.get("package", {})
    workspace_dependencies = workspace.get("dependencies", {})

    for field in PACKAGE_FIELDS:
        if field not in workspace_package:
            failures.append(f"workspace.package.{field} is not defined")

    member_manifests = [ROOT / member / "Cargo.toml" for member in workspace["members"]]
    member_packages: dict[str, Path] = {}
    used_dependencies: set[str] = set()
    dependency_edges = 0
    for manifest_path in member_manifests:
        relative_manifest = manifest_path.relative_to(ROOT)
        if not manifest_path.is_file():
            failures.append(f"workspace member manifest does not exist: {relative_manifest}")
            continue

        manifest = load_manifest(manifest_path)
        package = manifest.get("package", {})
        package_name = package.get("name")
        if not isinstance(package_name, str):
            failures.append(f"package.name is missing: {relative_manifest}")
            continue
        if package_name in member_packages:
            failures.append(
                f"duplicate workspace package name {package_name!r}: "
                f"{relative_manifest} and "
                f"{member_packages[package_name].relative_to(ROOT)}"
            )
        member_packages[package_name] = manifest_path

        for field in PACKAGE_FIELDS:
            if package.get(field) != {"workspace": True}:
                failures.append(
                    f"package.{field} must inherit from the workspace: {relative_manifest}"
                )

        for section, dependencies in dependency_tables(manifest):
            for dependency, specification in dependencies.items():
                dependency_edges += 1
                used_dependencies.add(dependency)
                if dependency not in workspace_dependencies:
                    failures.append(
                        f"{section}.{dependency} is missing from workspace.dependencies: "
                        f"{relative_manifest}"
                    )
                if not isinstance(specification, dict) or specification.get("workspace") is not True:
                    failures.append(
                        f"{section}.{dependency} must use workspace = true: "
                        f"{relative_manifest}"
                    )
                    continue
                forbidden_keys = sorted(
                    set(specification).difference(MEMBER_OWNED_DEPENDENCY_KEYS)
                )
                if forbidden_keys:
                    failures.append(
                        f"{section}.{dependency} repeats workspace-owned keys "
                        f"{forbidden_keys}: {relative_manifest}"
                    )
                workspace_specification = workspace_dependencies.get(dependency)
                workspace_features = (
                    set(workspace_specification.get("features", []))
                    if isinstance(workspace_specification, dict)
                    else set()
                )
                redundant_features = sorted(
                    workspace_features.intersection(specification.get("features", []))
                )
                if redundant_features:
                    failures.append(
                        f"{section}.{dependency} repeats workspace features "
                        f"{redundant_features}: {relative_manifest}"
                    )

    for package_name, manifest_path in sorted(member_packages.items()):
        specification = workspace_dependencies.get(package_name)
        if not isinstance(specification, dict) or "path" not in specification:
            failures.append(
                f"workspace.dependencies.{package_name} must define the member path"
            )
            continue
        dependency_manifest = ROOT / specification["path"] / "Cargo.toml"
        if dependency_manifest.resolve() != manifest_path.resolve():
            failures.append(
                f"workspace.dependencies.{package_name}.path points to "
                f"{dependency_manifest.parent.relative_to(ROOT)}, expected "
                f"{manifest_path.parent.relative_to(ROOT)}"
            )

    unused_catalog_entries = sorted(
        set(workspace_dependencies).difference(used_dependencies, member_packages)
    )
    for dependency in unused_catalog_entries:
        failures.append(
            f"workspace.dependencies.{dependency} is unused by every workspace member"
        )

    if failures:
        print("workspace dependency checks failed:", file=sys.stderr)
        print("\n".join(f"- {failure}" for failure in failures), file=sys.stderr)
        return 1

    print(
        "workspace dependency checks passed "
        f"({len(member_manifests)} members, {dependency_edges} dependency edges, "
        f"{len(workspace_dependencies)} catalog entries)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
