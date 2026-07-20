from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Iterable, Mapping

from kairos.storage.data_lake import write_json


class MassiveReferenceStore:
    """Versioned code tables for exchanges, conditions, calendars and provider metadata."""

    def __init__(self, root: str | Path = "data/reference/provider=massive") -> None:
        self.root = Path(root)

    def save(self, name: str, rows: Iterable[Mapping[str, object]], *, source_receipt: str | Iterable[str]) -> dict[str, object]:
        values = sorted((dict(item) for item in rows), key=lambda item: json.dumps(item, sort_keys=True, default=str))
        content = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
        digest = sha256(content).hexdigest()
        directory = self.root / name / f"version={digest}"
        directory.mkdir(parents=True, exist_ok=True)
        payload = directory / "records.json"
        if not payload.exists():
            temporary = payload.with_suffix(".json.tmp"); temporary.write_bytes(content); temporary.replace(payload)
        receipts = (source_receipt,) if isinstance(source_receipt, str) else tuple(str(item) for item in source_receipt)
        manifest = {"manifest_version": 1, "provider": "massive", "name": name, "records": len(values),
                    "sha256": digest, "source_receipt": receipts[0] if receipts else None, "source_receipts": list(receipts),
                    "generated_at": datetime.now(timezone.utc).isoformat()}
        write_json(directory / "manifest.json", manifest)
        return manifest
