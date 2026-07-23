from __future__ import annotations


def build_workspace(ws, params):
    profile = str(params.get("workspace_profile") or "position-lab")
    attachments = ws.attachments.use_profile(profile)
    hl_orderbook = attachments.as_orderbook("hl_orderbook", name="hl_orderbook")
    hl_funding = attachments.as_funding("hl_funding", name="hl_funding")
    binance_spot_book = attachments.as_orderbook("binance_spot_book", name="binance_spot_book")
    return ws.project(
        market=(hl_orderbook, hl_funding, binance_spot_book),
        portfolio=("binance_spot", "hyperliquid_perp"),
    )
