from __future__ import annotations


def build_workspace(ws, params):
    profile = str(params.get("workspace_profile") or "market-print")
    attachments = ws.attachments.use_profile(profile)
    market = attachments.as_ohlcv("market", required=False)
    momentum = ws.features.momentum(name="momentum", source=market, window=int(params.get("fast", "20")))
    volatility = ws.features.realized_volatility(name="realized_volatility", source=market, window=int(params.get("slow", "50")))
    return ws.project(market=(market,), features=(momentum, volatility))
