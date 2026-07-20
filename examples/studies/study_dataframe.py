"""Open a Study-bound Dataset Release as a DataFrame without storage plumbing."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kairospy.study_platform import open_study


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake-root", default="example-output/first-study")
    parser.add_argument("--study-id", default="btc-sma-first")
    parser.add_argument("--version", default="1.0.0")
    args = parser.parse_args()

    study = open_study(args.study_id, root=args.lake_root, version=args.version)
    frame = study.data.pandas(columns=("available_time", "open", "high", "low", "close", "volume"))

    print(frame.head(10).to_string(index=False))
    print("\nProfile")
    for name, value in study.profile().as_dict().items():
        print(f"  {name}: {value}")


if __name__ == "__main__":
    main()
