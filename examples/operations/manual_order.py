"""Scenario 7: explicitly audited manual order through the formal runtime."""

from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime,timedelta,timezone
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from kairospy.__main__ import main
from kairospy.application import build_simulated_spot_catalog
from kairospy.domain.identity import AccountKey,AccountType,AssetId,InstitutionId,InstrumentId
from kairospy.reference import ReferenceCatalogRepository


def run(root:Path)->dict[str,object]:
    instrument=InstrumentId("crypto:simulated:spot:BTCUSDT")
    account=AccountKey(InstitutionId("simulated"),"default",AccountType.CRYPTO_SPOT)
    catalog=build_simulated_spot_catalog(instrument_id=instrument,account=account,base_asset=AssetId("BTC"),
        quote_asset=AssetId("USDT"),effective_from=datetime.now(timezone.utc)-timedelta(days=1))
    catalog_path=root/"reference.json";ReferenceCatalogRepository(catalog_path).save(catalog)
    event_path=root/"manual-events.jsonl";runtime_db=root/"manual-runtime.sqlite3";output=StringIO()
    with redirect_stdout(output):
        code=main(["--reference-catalog-path",str(catalog_path),"--event-log-path",str(event_path),
            "--runtime-db",str(runtime_db),"order","submit","--venue","simulated","--environment","testnet",
            "--instrument",instrument.value,"--side","buy","--quantity","0.01","--limit-price","50000",
            "--actor","acceptance-operator","--reason","scenario-7 acceptance"])
    audit=event_path.read_text(encoding="utf-8")
    return {"exit_code":code,"accepted":"Accepted:" in output.getvalue(),"actor_recorded":"acceptance-operator" in audit,
        "reason_recorded":"scenario-7 acceptance" in audit,"runtime_database_exists":runtime_db.exists()}


def main_example():
    with tempfile.TemporaryDirectory() as directory:print(json.dumps(run(Path(directory)),indent=2,sort_keys=True))


if __name__=="__main__":main_example()
