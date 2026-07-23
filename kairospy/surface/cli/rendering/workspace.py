from __future__ import annotations

import argparse
import json

from kairospy.infrastructure.storage.codec import to_primitive


def emit_workspace_payload(args: argparse.Namespace, payload: dict[str, object]) -> None:
    print(json.dumps(to_primitive(payload), ensure_ascii=False, indent=2, sort_keys=True))
