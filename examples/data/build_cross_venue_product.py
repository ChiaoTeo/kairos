import argparse
from decimal import Decimal

from kairospy.data import ConsolidatedTradeBuilder, ConsolidatedTradeInput, ConsolidatedTradePolicy


parser = argparse.ArgumentParser()
parser.add_argument("--source", action="append", required=True,
                    help="dataset,provider,venue,instrument_type,currency; repeat for each venue")
parser.add_argument("--fx", action="append", required=True, help="currency=rate_to_target")
parser.add_argument("--target-currency", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--start", required=True)
parser.add_argument("--end", required=True)
args = parser.parse_args()

inputs = tuple(ConsolidatedTradeInput(*value.split(",")) for value in args.source)
rates = {name: Decimal(value) for name, value in (item.split("=", 1) for item in args.fx)}
release = ConsolidatedTradeBuilder("data").build(
    args.output, args.output, inputs,
    ConsolidatedTradePolicy("explicit_cross_venue_union", "1", args.target_currency, rates),
    start=args.start, end=args.end,
)
print(release.release_id, release.content_hash)
