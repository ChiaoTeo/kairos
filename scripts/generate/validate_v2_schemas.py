#!/usr/bin/env python3
"""Validate the v2 schema boundary before generating language bindings.

This is intentionally stdlib-only so it can run in the same environments as
the FlatBuffers generator.  The registry is the admission list for roots;
the checks below keep the common/system/business ownership boundaries
executable without turning generated bindings into application models.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "schemas"
REGISTRY = SCHEMAS / "v2" / "registry.md"

INCLUDE_RE = re.compile(r'^include\s+"([^"]+)";')
NAMESPACE_RE = re.compile(r'^namespace\s+([^;]+);')
ROOT_RE = re.compile(r'^root_type\s+(\w+);')
IDENTIFIER_RE = re.compile(r'^file_identifier\s+"([^"]{4})";')
REGISTRY_ROW_RE = re.compile(
    r'^\|\s*(?:DRAFT|ACTIVE)\s*\|\s*[^|]+\|\s*[^|]+\|\s*([^|]+)\|\s*([^|]+)\|'
)


def schema_info(path: Path) -> tuple[str, str, str, list[str]] | None:
    text = path.read_text(encoding="utf-8").splitlines()
    namespace = next((m.group(1) for line in text if (m := NAMESPACE_RE.match(line))), None)
    root = next((m.group(1) for line in text if (m := ROOT_RE.match(line))), None)
    identifier = next(
        (m.group(1) for line in text if (m := IDENTIFIER_RE.match(line))), None
    )
    if root is None or identifier is None:
        return None
    includes = [m.group(1) for line in text if (m := INCLUDE_RE.match(line))]
    return namespace or "", root, identifier, includes


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def main() -> int:
    errors: list[str] = []
    roots: dict[tuple[str, str], Path] = {}
    identifiers: dict[str, Path] = {}

    v2_files = sorted((SCHEMAS / "v2").glob("**/*.fbs"))
    for path in v2_files:
        info = schema_info(path)
        if info is None:
            continue
        namespace, root, identifier, includes = info
        key = (root, identifier)
        if key in roots:
            fail(errors, f"duplicate root {root}/{identifier}: {path} and {roots[key]}")
        roots[key] = path
        if identifier in identifiers:
            fail(errors, f"duplicate file identifier {identifier}: {path} and {identifiers[identifier]}")
        identifiers[identifier] = path

        relative = path.relative_to(SCHEMAS / "v2").parts
        owner = relative[0]
        if "/events/" in path.as_posix() and "../../common/metadata.fbs" not in includes and "../../../common/metadata.fbs" not in includes:
            fail(errors, f"event root does not include common metadata: {path.relative_to(ROOT)}")
        if "/views/" in path.as_posix() and "../../common/metadata.fbs" not in includes and "../common/metadata.fbs" not in includes:
            fail(errors, f"view root does not include common metadata: {path.relative_to(ROOT)}")
        if owner == "system":
            for include in includes:
                if "common/" not in include and not include.startswith("types/"):
                    fail(errors, f"system schema imports non-common/non-system schema {include}: {path.relative_to(ROOT)}")
        if owner not in {"common", "system"}:
            for include in includes:
                if "common/" in include or include.startswith("types/") or include.startswith("../types/") or include.startswith("../../types/"):
                    continue
                if include.startswith("../"):
                    fail(errors, f"business schema crosses owner boundary through {include}: {path.relative_to(ROOT)}")

    registry_text = REGISTRY.read_text(encoding="utf-8").splitlines()
    registered: set[tuple[str, str]] = set()
    for line in registry_text:
        match = REGISTRY_ROW_RE.match(line)
        if not match:
            continue
        semantic_root, identifier = (value.strip().strip("`") for value in match.groups())
        if identifier == "control.openapi.yaml":
            continue
        registered.add((semantic_root, identifier))

    for key, path in roots.items():
        if key not in registered:
            fail(errors, f"FlatBuffers root missing from registry: {path.relative_to(ROOT)} ({key[0]}/{key[1]})")
    for key in registered:
        if key not in roots:
            fail(errors, f"registry root has no v2 FlatBuffers schema: {key[0]}/{key[1]}")

    if errors:
        print("v2 schema validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"v2 schema validation passed ({len(roots)} FlatBuffers roots)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
