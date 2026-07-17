import argparse

from trading.data import OutputFormat, ResearchDataClient


parser = argparse.ArgumentParser()
parser.add_argument("dataset")
parser.add_argument("--provider", required=True)
parser.add_argument("--venue", required=True)
parser.add_argument("--start", required=True)
parser.add_argument("--end", required=True)
args = parser.parse_args()

# Provider and venue are both explicit. A Deribit release can never silently
# satisfy this Binance request.
frame = ResearchDataClient("data").get(
    args.dataset,
    provider=args.provider,
    venue=args.venue,
    start=args.start,
    end=args.end,
).collect(OutputFormat.POLARS)
print(frame)
