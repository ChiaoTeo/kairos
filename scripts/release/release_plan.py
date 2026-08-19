#!/usr/bin/env python3
"""Validate release metadata and decide whether a PyPI publish is required."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PYPI_PROJECT_URL = "https://pypi.org/pypi/kairospy/json"


class ReleasePlanError(RuntimeError):
    """Release metadata or repository state is unsafe to publish."""


@dataclass(frozen=True)
class ReleaseMetadata:
    name: str
    version: str
    workspace_packages: tuple[str, ...]

    @property
    def tag(self) -> str:
        return f"v{self.version}"


@dataclass(frozen=True)
class ReleasePlan:
    metadata: ReleaseMetadata
    publish: bool
    tag_at_head: bool


def _read_toml(path: Path) -> dict[str, object]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _stable_version(version: str) -> tuple[int, int, int]:
    parts = version.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ReleasePlanError(
            f"release version must be a stable MAJOR.MINOR.PATCH value: {version!r}"
        )
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


def load_release_metadata(root: Path = ROOT) -> ReleaseMetadata:
    pyproject = _read_toml(root / "pyproject.toml")
    project = pyproject.get("project")
    if not isinstance(project, dict):
        raise ReleasePlanError("pyproject.toml is missing [project]")
    name = project.get("name")
    python_version = project.get("version")
    if not isinstance(name, str) or not isinstance(python_version, str):
        raise ReleasePlanError("Python project name and version must be strings")
    _stable_version(python_version)

    cargo_root = _read_toml(root / "Cargo.toml")
    workspace = cargo_root.get("workspace")
    if not isinstance(workspace, dict):
        raise ReleasePlanError("Cargo.toml is missing [workspace]")
    package_defaults = workspace.get("package")
    if not isinstance(package_defaults, dict):
        raise ReleasePlanError("Cargo.toml is missing [workspace.package]")
    cargo_version = package_defaults.get("version")
    if cargo_version != python_version:
        raise ReleasePlanError(
            "Python and Rust workspace versions differ: "
            f"{python_version!r} != {cargo_version!r}"
        )

    members = workspace.get("members")
    if not isinstance(members, list) or not all(
        isinstance(item, str) for item in members
    ):
        raise ReleasePlanError("workspace.members must be a list of paths")

    package_names: list[str] = []
    for member in members:
        manifest = _read_toml(root / member / "Cargo.toml")
        package = manifest.get("package")
        if not isinstance(package, dict) or not isinstance(package.get("name"), str):
            raise ReleasePlanError(f"{member}/Cargo.toml is missing package.name")
        member_version = package.get("version")
        if member_version != {"workspace": True}:
            raise ReleasePlanError(
                f"{member}/Cargo.toml must inherit version.workspace = true"
            )
        package_names.append(package["name"])

    lock = _read_toml(root / "Cargo.lock")
    locked_packages = lock.get("package")
    if not isinstance(locked_packages, list):
        raise ReleasePlanError("Cargo.lock is missing package records")
    locked_workspace_versions = {
        package.get("name"): package.get("version")
        for package in locked_packages
        if isinstance(package, dict)
        and package.get("name") in package_names
        and "source" not in package
    }
    mismatches = {
        package: locked_workspace_versions.get(package)
        for package in package_names
        if locked_workspace_versions.get(package) != python_version
    }
    if mismatches:
        details = ", ".join(
            f"{package}={version!r}" for package, version in sorted(mismatches.items())
        )
        raise ReleasePlanError(f"Cargo.lock workspace versions are stale: {details}")

    return ReleaseMetadata(name, python_version, tuple(sorted(package_names)))


def published_version(url: str = PYPI_PROJECT_URL) -> str | None:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise ReleasePlanError(f"PyPI returned HTTP {error.code}") from error
    except (OSError, ValueError) as error:
        raise ReleasePlanError(f"could not read PyPI release state: {error}") from error
    try:
        version = payload["info"]["version"]
    except (KeyError, TypeError) as error:
        raise ReleasePlanError("PyPI response is missing info.version") from error
    if not isinstance(version, str):
        raise ReleasePlanError("PyPI info.version is not a string")
    return version


def _git(*arguments: str, root: Path = ROOT, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=check,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def tag_commit(tag: str, root: Path = ROOT) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", f"refs/tags/{tag}^{{}}"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def create_plan(
    metadata: ReleaseMetadata,
    *,
    current_pypi_version: str | None,
    check_git: bool,
    root: Path = ROOT,
) -> ReleasePlan:
    desired = _stable_version(metadata.version)
    published = (
        _stable_version(current_pypi_version)
        if current_pypi_version is not None
        else None
    )
    if published is not None and desired < published:
        raise ReleasePlanError(
            f"release version {metadata.version} is older than PyPI {current_pypi_version}"
        )
    publish = published is None or desired > published

    head = _git("rev-parse", "HEAD", root=root) if check_git else ""
    existing_tag_commit = tag_commit(metadata.tag, root) if check_git else None
    tag_at_head = existing_tag_commit == head if existing_tag_commit else False
    if publish and existing_tag_commit and not tag_at_head:
        raise ReleasePlanError(
            f"{metadata.tag} already points to {existing_tag_commit}, not {head}"
        )
    if check_git:
        contains = subprocess.run(
            ["git", "merge-base", "--is-ancestor", "HEAD", "origin/main"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        if contains.returncode != 0:
            raise ReleasePlanError("release commit is not contained in origin/main")
    return ReleasePlan(metadata, publish, tag_at_head)


def _write_github_output(path: Path, plan: ReleasePlan) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"version={plan.metadata.version}\n")
        handle.write(f"tag={plan.metadata.tag}\n")
        handle.write(f"publish={str(plan.publish).lower()}\n")
        handle.write(f"tag_at_head={str(plan.tag_at_head).lower()}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-pypi", action="store_true")
    parser.add_argument("--check-git", action="store_true")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    try:
        metadata = load_release_metadata()
        current = published_version() if args.check_pypi else None
        plan = create_plan(
            metadata,
            current_pypi_version=current,
            check_git=args.check_git,
        )
    except ReleasePlanError as error:
        print(f"release plan failed: {error}", file=sys.stderr)
        return 1
    if args.github_output:
        _write_github_output(args.github_output, plan)
    print(
        json.dumps(
            {
                "name": metadata.name,
                "version": metadata.version,
                "tag": metadata.tag,
                "publish": plan.publish,
                "tag_at_head": plan.tag_at_head,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
