from __future__ import annotations


class FundingArbStrategy:
    strategy_id = "cross-venue-funding-arb"

    def __init__(self, projection=None, params=None):
        self.params = dict(params or {})

    def on_start(self, context):
        return ()

    def on_market(self, context):
        carry = context.features.factor("expected_funding_carry")
        liquidity = context.features.factor("cross_venue_liquidity")
        hedge_error = context.features.factor("hedge_error")
        target_notional = str(self.params.get("target_notional") or "10000")
        min_carry_bps = str(self.params.get("min_expected_carry_bps") or "8")
        max_hedge_error_bps = str(self.params.get("max_hedge_error_bps") or "15")
        max_slippage_bps = str(self.params.get("max_slippage_bps") or "5")
        min_liquidation_buffer_bps = str(self.params.get("min_liquidation_buffer_bps") or "500")
        return (
            {
                "type": "pair_trade_check",
                "strategy_id": self.strategy_id,
                "target_notional": target_notional,
                "legs": {
                    "short": {
                        "venue": "hyperliquid",
                        "instrument": "BTC-PERP",
                        "side": "sell",
                        "product": "perpetual",
                    },
                    "long": {
                        "venue": "binance",
                        "instrument": "BTCUSDT",
                        "side": "buy",
                        "product": "spot",
                    },
                },
                "features": {
                    "expected_funding_carry": dict(carry.values),
                    "cross_venue_liquidity": dict(liquidity.values),
                    "hedge_error": dict(hedge_error.values),
                },
                "open_when": (
                    f"expected_funding_carry_bps >= {min_carry_bps}",
                    f"estimated_slippage_bps <= {max_slippage_bps}",
                    f"abs(hedge_error_bps) <= {max_hedge_error_bps}",
                    f"hyperliquid_liquidation_buffer_bps >= {min_liquidation_buffer_bps}",
                ),
                "reduce_when": (
                    "funding_carry_after_cost <= 0",
                    f"abs(hedge_error_bps) > {max_hedge_error_bps}",
                    "liquidity_guard_failed",
                    "margin_buffer_guard_failed",
                    "venue_or_transfer_stale",
                ),
                "risk_checks": {
                    "delta_hedged": {"max_hedge_error_bps": max_hedge_error_bps},
                    "funding_positive_after_cost": {"min_expected_carry_bps": min_carry_bps},
                    "liquidity_sufficient": {"max_slippage_bps": max_slippage_bps},
                    "margin_buffer_sufficient": {"min_liquidation_buffer_bps": min_liquidation_buffer_bps},
                    "transfer_not_counted_until_confirmed": {"required": True},
                },
                "orders": (
                    {
                        "venue": "hyperliquid",
                        "instrument": "BTC-PERP",
                        "side": "sell",
                        "notional": target_notional,
                        "reduce_only": False,
                        "blocked_until": "live_execution_keys_and_risk_inputs_verified",
                    },
                    {
                        "venue": "binance",
                        "instrument": "BTCUSDT",
                        "side": "buy",
                        "notional": target_notional,
                        "reduce_only": False,
                        "blocked_until": "live_execution_keys_and_risk_inputs_verified",
                    },
                ),
                "treasury_transfer_intent": {
                    "type": "rebalance_margin",
                    "from": "binance",
                    "to": "hyperliquid",
                    "asset": "USDC",
                    "trigger": "hyperliquid_margin_buffer_bps below threshold or target_notional increases",
                    "blocked_until": "transfer_route_confirmed",
                },
                "decision": "hold_until_live_risk_inputs_are_bound",
            },
        )

    def on_fill(self, fill, context):
        return ()

    def on_end(self, context):
        return ()
