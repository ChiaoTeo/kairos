"""Show which runtime components change while strategy semantics remain fixed."""

from __future__ import annotations

import json

from kairos.application import (
    backtest_composition, historical_simulation_composition, live_composition,
    paper_trading_composition, research_composition,
)


def main() -> None:
    compositions = (
        research_composition(), backtest_composition(), historical_simulation_composition(),
        paper_trading_composition("binance"), live_composition("binance", "binance-testnet-or-live"),
    )
    print(json.dumps({
        "invariant": "same canonical facts -> projectors -> strategy -> intents -> risk semantics",
        "modes": [
            {**item.manifest(), "composition_hash": item.composition_hash}
            for item in compositions
        ],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
