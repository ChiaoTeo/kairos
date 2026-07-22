from __future__ import annotations

from kairospy import Workspace


def main() -> None:
    workspace = Workspace.open_or_create("market-print")
    query = workspace.data.get("market")
    df = query.collect("pandas")
    print(df)


if __name__ == "__main__":
    main()

