from __future__ import annotations

import importlib
import re
import subprocess
import sys
from pathlib import Path

from kairospy.infrastructure.transport import generated_spec


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "schemas" / "v2"
ROOT_RE = re.compile(r"^root_type\s+(\w+);", re.MULTILINE)
IDENTIFIER_RE = re.compile(r'^file_identifier\s+"([^"]{4})";', re.MULTILINE)
NAMESPACE_RE = re.compile(r"^namespace\s+([^;]+);", re.MULTILINE)


def test_transport_spec_is_fresh() -> None:
    subprocess.run(
        [sys.executable, "scripts/generate/generate_transport_spec.py", "--check"],
        cwd=ROOT,
        check=True,
    )


def test_transport_limits_cover_the_u32_bridge_frame() -> None:
    assert 0 < generated_spec.DEFAULT_MAX_PAYLOAD_LEN <= 0xFFFF_FFFF
    assert generated_spec.TRANSPORT_SPEC_VERSION == 1
    assert len(generated_spec.TRANSPORT_FINGERPRINT) == 64


def test_every_schema_root_matches_its_generated_python_identifier() -> None:
    checked = 0
    for schema in sorted(SCHEMA_ROOT.glob("**/*.fbs")):
        source = schema.read_text()
        root_match = ROOT_RE.search(source)
        identifier_match = IDENTIFIER_RE.search(source)
        namespace_match = NAMESPACE_RE.search(source)
        if root_match is None or identifier_match is None or namespace_match is None:
            continue
        root = root_match.group(1)
        identifier = identifier_match.group(1).encode()
        namespace = namespace_match.group(1)
        module = importlib.import_module(
            "kairospy.infrastructure.transport.generated." + namespace + "." + root
        )
        generated_root = getattr(module, root)
        has_identifier = getattr(generated_root, f"{root}BufferHasIdentifier")
        assert has_identifier(b"\0\0\0\0" + identifier, 0), schema.relative_to(ROOT)
        checked += 1
    assert checked > 0
