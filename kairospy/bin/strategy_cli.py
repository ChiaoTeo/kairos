from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from kairospy.application.system import UnixRestClient
from kairospy.application.workspace import WorkspaceApplication


def main() -> int:
    parser = argparse.ArgumentParser(prog="kairospy-strategy-cli")
    parser.add_argument("command", choices=("status", "start", "enable", "pause", "resume", "stop"))
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--launch-id", required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--mode", default="paper")
    args = parser.parse_args()
    workspace = WorkspaceApplication().open(args.workspace)
    socket = workspace.paths.launch_socket(args.mode, args.launch_id, args.instance_id)
    method = "GET" if args.command == "status" else "POST"
    path = "/v1/status" if args.command == "status" else f"/v1/{args.command}"
    value = asyncio.run(UnixRestClient(socket).request(method, path))
    import json
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
