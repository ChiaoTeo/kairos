from __future__ import annotations

import json
from pathlib import Path

from trading.domain.ledger import Ledger, LedgerTransaction
from trading.storage.codec import from_primitive, to_primitive


class LedgerRepository:
    def __init__(self, path: str | Path = "data/ledger/ledger.json") -> None:
        self.path = Path(path)

    def save(self, ledger: Ledger) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        value = {"schema_version": 1, "transactions": to_primitive(ledger.transactions)}
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.path)
        return self.path

    def load(self) -> Ledger:
        ledger = Ledger()
        if not self.path.exists():
            return ledger
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if value.get("schema_version") != 1:
            raise ValueError("unsupported ledger schema version")
        for item in value["transactions"]:
            ledger.post(from_primitive(item, LedgerTransaction))
        return ledger
