"""Run the governed SMA Strategy through the durable historical-simulation runtime."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from examples.backtest.governed_sma import canonical_events, fixture_bars
from kairos.application import build_simulated_spot_catalog, run_sma_historical_simulation
from kairos.domain.identity import AccountKey, AccountType, AssetId, InstitutionId
from kairos.features import SmaFactorConfig


async def run(root: Path) -> dict[str, object]:
    bars = fixture_bars()
    instrument = bars[0].instrument_id
    account = AccountKey(InstitutionId("simulated"), "sma-example", AccountType.CRYPTO_SPOT)
    catalog = build_simulated_spot_catalog(
        instrument_id=instrument, account=account, base_asset=AssetId("BTC"),
        quote_asset=AssetId("USDT"), effective_from=bars[0].start-timedelta(days=1),
    )
    result = await run_sma_historical_simulation(
        root=root, events=tuple(canonical_events(bars)), catalog=catalog,
        instrument_id=instrument, account=account, cash_asset=AssetId("USDT"),
        initial_cash=Decimal("100000"), factor_config=SmaFactorConfig(5, 15),
        input_identity="fixture:sma-bars-v1",
    )
    return {
        "mode": "historical-simulation",
        "bars": len(bars),
        "factor_hash": result.strategy_run.factor_hash,
        "decision_hash": result.strategy_run.decision_hash,
        "intent_hash": result.strategy_run.intent_hash,
        "orders": result.orders,
        "fills": result.fills,
        "final_cash": str(result.final_cash),
        "final_position": str(result.final_position),
        "restart_ready": result.restart_ready,
        "runtime_database_exists": result.runtime_database.exists(),
    }


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        print(json.dumps(asyncio.run(run(Path(directory))), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
