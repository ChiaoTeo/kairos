#!/usr/bin/env python3
"""Validate the repository's durable documentation boundary and local links."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
IGNORED_TREES = {
    ".agent-work",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".venv",
    "node_modules",
    "target",
}
FORBIDDEN_DOC_DIRECTORIES = ("generated", "proposal", "proposals", "reference")
REQUIRED_DOC_FILES = (
    "README.md",
    "architecture/README.md",
    "decisions/README.md",
    "guides/operations.md",
    "integrations/README.md",
)
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def markdown_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*.md"):
        relative = path.relative_to(ROOT)
        if any(
            part in IGNORED_TREES or part.startswith("target")
            for part in relative.parts
        ):
            continue
        files.append(path)
    return sorted(files)


def local_link_target(raw_target: str) -> str | None:
    target = raw_target.strip()
    if not target or target.startswith(("#", "http://", "https://", "mailto:")):
        return None
    if target.startswith("<") and ">" in target:
        target = target[1 : target.index(">")]
    else:
        target = target.split(maxsplit=1)[0]
    target = unquote(target.split("#", 1)[0].split("?", 1)[0])
    return target or None


def main() -> int:
    failures: list[str] = []

    for directory in FORBIDDEN_DOC_DIRECTORIES:
        path = DOCS / directory
        if path.exists():
            failures.append(
                f"forbidden documentation directory exists: {path.relative_to(ROOT)}"
            )

    for relative in REQUIRED_DOC_FILES:
        path = DOCS / relative
        if not path.is_file():
            failures.append(
                f"required documentation file is missing: {path.relative_to(ROOT)}"
            )

    for path in sorted(DOCS.rglob("*.html")):
        failures.append(
            "generated HTML must be written under target/docs, not committed: "
            f"{path.relative_to(ROOT)}"
        )

    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    if not any(line.strip().rstrip("/") == "/.agent-work" for line in gitignore):
        failures.append(".gitignore must ignore /.agent-work/")

    root_readme_lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
    if len(root_readme_lines) > 250:
        failures.append(
            "README.md is an entry point, not the operation manual "
            f"({len(root_readme_lines)} > 250 lines)"
        )

    for path in sorted((DOCS / "decisions").glob("[0-9][0-9][0-9][0-9]-*.md")):
        text = path.read_text(encoding="utf-8")
        for marker in ("- Status:", "## Context", "## Decision", "## Consequences"):
            if marker not in text:
                failures.append(
                    f"decision is missing {marker!r}: {path.relative_to(ROOT)}"
                )

    files = markdown_files()
    for path in files:
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for match in LINK_RE.finditer(line):
                target = local_link_target(match.group(1))
                if target is None:
                    continue
                resolved = (path.parent / target).resolve()
                if not resolved.exists():
                    failures.append(
                        f"broken local link: {path.relative_to(ROOT)}:{line_number} -> {target}"
                    )

    if failures:
        print("documentation checks failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(f"documentation checks passed ({len(files)} Markdown files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
