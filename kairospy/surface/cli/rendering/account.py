from __future__ import annotations

import argparse
import json


def _account_credential_fields(name: str, raw: object) -> tuple[str, ...]:
    if isinstance(raw, dict):
        explicit = tuple(
            field for field in (
                "api_key",
                "api_secret",
                "passphrase",
                "private_key",
                "account_address",
                "host",
                "port",
                "client_id",
            )
            if field in raw
        )
        if explicit:
            return explicit
        kind = str(raw.get("kind") or "")
        if kind == "private_key_account":
            return ("private_key", "account_address")
        if kind == "api_key_secret_passphrase":
            return ("api_key", "api_secret", "passphrase")
        if kind == "api_key_secret":
            return ("api_key", "api_secret")
    if name.startswith("hyperliquid_"):
        return ("private_key", "account_address")
    if name.startswith("ibkr_"):
        return ("host", "port", "client_id")
    return ("api_key", "api_secret")

def _emit_accounts_payload(args: argparse.Namespace, payload: dict[str, object]) -> None:
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"Account: {payload.get('account')}")
    print(f"Status: {payload.get('status')}")
    if payload.get("account_ref"):
        print(f"Account Ref: {payload.get('account_ref')}")
    if payload.get("provider"):
        print(f"Provider: {payload.get('provider')}")
    for issue in payload.get("issues", ()):
        if isinstance(issue, dict):
            print(f"Issue: {issue.get('code')}")
