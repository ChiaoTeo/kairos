"""Scenario 7: explicitly audited manual order through the formal runtime."""

from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from kairospy.surface.cli.main import main
from kairospy.identity import AccountRef,AccountType,AssetId,InstitutionId,InstrumentId,VenueId
from kairospy.reference import (
    AssetDefinition,AssetType,BrokerId,CryptoSpotSpec,ExecutionRoute,ListingDefinition,ListingId,ProductType,
    ReferenceCatalog,ReferenceCatalogRepository,TradingRules,VenueDefinition,VenueType,
    RouteId,
)
from kairospy.reference.factory import publish_instrument


def _build_simulated_spot_catalog(instrument:InstrumentId,effective_from:datetime)->ReferenceCatalog:
    catalog=ReferenceCatalog()
    publish_instrument(
        catalog,
        instrument_id=instrument,
        instrument_type=ProductType.CRYPTO_SPOT,
        display_name="BTC/USDT",
        contract_spec=CryptoSpotSpec(AssetId("BTC"),AssetId("USDT"),Decimal("10")),
        trading_currency=AssetId("USDT"),
        listings=(ListingDefinition(
            ListingId("listing:simulated:BTCUSDT"),instrument,VenueId("simulated"),"BTCUSDT",AssetId("USDT"),
            TradingRules(Decimal("0.01"),Decimal("0.0001"),Decimal("0.0001"),minimum_notional=Decimal("10")),
            effective_from,venue_instrument_id="BTCUSDT",
        ),),
        effective_from=effective_from,
        asset_definitions=(
            AssetDefinition(AssetId("BTC"),AssetType.CRYPTO,"Bitcoin",effective_from,decimals=8),
            AssetDefinition(AssetId("USDT"),AssetType.CRYPTO,"Tether USD",effective_from,decimals=6),
        ),
        venue_definitions=(VenueDefinition(VenueId("simulated"),VenueType.CRYPTO_EXCHANGE,"Simulated","UTC",effective_from),),
    )
    catalog.routes.add(ExecutionRoute(
        RouteId("route:simulated:default"),
        BrokerId("simulated"),
        AccountRef(InstitutionId("simulated"),"default",AccountType.CRYPTO_SPOT),
        ListingId("listing:simulated:BTCUSDT"),
        effective_from,
    ))
    return catalog


def run(root:Path)->dict[str,object]:
    instrument=InstrumentId("crypto:simulated:spot:BTCUSDT")
    catalog=_build_simulated_spot_catalog(instrument,datetime.now(timezone.utc)-timedelta(days=1))
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
