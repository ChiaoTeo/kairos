from __future__ import annotations


def build_workspace(ws, params):
    ws.attachments.use_profile(params.get("workspace_profile", "funding-arb"))

    hl_mark = ws.use("hl_perp_mark").as_mark_price(name="hl_mark")
    hl_funding = ws.use("hl_funding").as_funding(name="hl_funding", lookback="7d")
    hl_book = ws.use("hl_orderbook").as_orderbook(name="hl_book")
    bn_book = ws.use("binance_spot_book").as_orderbook(name="bn_book")

    basis = ws.features.basis(
        name="hl_bn_basis",
        short_leg=hl_mark,
        long_leg=bn_book,
        fees=params.get("fee_model", "default"),
    )
    expected_carry = ws.features.expected_funding_carry(
        name="expected_funding_carry",
        funding=hl_funding,
        basis=basis,
        horizon=params.get("carry_horizon", "8h"),
    )
    liquidity = ws.features.cross_venue_liquidity(
        name="cross_venue_liquidity",
        short_book=hl_book,
        long_book=bn_book,
        target_notional=params.get("target_notional", "0"),
    )
    hedge_error = ws.features.hedge_error(
        name="hedge_error",
        short_leg=hl_mark,
        long_leg=bn_book,
    )

    return ws.project(
        market=[hl_mark, hl_book, bn_book],
        features=[basis, expected_carry, liquidity, hedge_error],
        portfolio=("hyperliquid_perp", "binance_spot"),
        treasury=("binance", "hyperliquid"),
    )
