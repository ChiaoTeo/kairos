from __future__ import annotations

from kairospy.surface.workbench.screens.flows.resources import account
from kairospy.surface.workbench.widgets import renderable_plain_text


def test_account_overview_is_rendered_as_business_tables() -> None:
    content = renderable_plain_text(
        account._account_result_renderable(
            "account.overview",
            {
                "identity": {
                    "account_id": "manual-live-readonly",
                    "alias": "Manual live",
                    "broker": "binance",
                    "exchange": "binance",
                    "environment": "live",
                },
                "connection": {"integration_adapter": "binance"},
                "profile": {
                    "configured_account_model": None,
                    "observed_account_model": "multiple",
                    "provider_account_model": "portfolio_margin",
                    "model_match": "match",
                    "unified": True,
                    "margin_mode": "cross",
                    "position_mode": "one_way",
                    "segments": [
                        {
                            "segment": "spot",
                            "completeness": "complete",
                            "freshness": "fresh",
                            "observed_account_model": "spot",
                            "provider_account_model": "spot",
                            "observed_at_unix_nanos": 123,
                            "issue": None,
                        }
                    ],
                },
                "permissions": {"effective_capabilities": ["read"]},
                "commercial": {"fee_summary_status": "not_queried", "vip_tier": None},
                "facts": {
                    "non_zero_balance_count": 2,
                    "collateral_count": 1,
                    "position_count": 0,
                    "earn_holding_count": None,
                    "open_order_count": 0,
                },
                "health": {
                    "source": "direct_provider",
                    "mode": "standalone",
                    "overall_status": "ready",
                    "freshness": "fresh",
                    "completeness": "complete",
                    "segments_requested": 1,
                    "segments_succeeded": 1,
                    "issues": [],
                },
            },
            title="manual-live-readonly · 账户运行结果",
        ),
        width=180,
    )

    for label in (
        "账户概览",
        "券商/托管方",
        "实测账户模式",
        "有效权限",
        "非零资产",
        "健康状态",
        "账户分区",
        "完整度",
    ):
        assert label in content
    assert "manual-live-readonly" in content
    assert "接入直连" in content
    assert "统一账户（Portfolio Margin）" in content
    assert "identity" not in content
    assert "profile" not in content
    assert "health" not in content


def test_empty_positions_explain_where_spot_assets_are_shown() -> None:
    content = renderable_plain_text(
        account._account_result_renderable(
            "account.positions",
            {
                "account_id": "manual-live-readonly",
                "source": "direct_provider",
                "mode": "standalone",
                "segments_requested": 3,
                "segments_succeeded": 3,
                "completeness": "complete",
                "positions": [],
                "outcomes": [
                    {"segment": "spot", "outcome": "complete", "message": None}
                ],
                "errors": [],
            },
            title="manual-live-readonly · 账户运行结果",
        ),
        width=180,
    )

    assert "账户 manual-live-readonly" in content
    assert "完整度 完整" in content
    assert "当前没有交易仓位" in content
    assert "资产与余额" in content
    for raw_field in ("positions", "outcomes", "errors"):
        assert raw_field not in content


def test_earn_holdings_are_rendered_as_business_columns() -> None:
    content = renderable_plain_text(
        account._account_result_renderable(
            "account.earn",
            {
                "account_id": "manual-live-readonly",
                "source": "direct_provider",
                "mode": "standalone",
                "completeness": "partial",
                "holdings": [
                    {
                        "segment": "earn",
                        "family": "staking",
                        "product_id": "eth-staking",
                        "asset": "ETH",
                        "principal": "1.5",
                        "redeemable_amount": "1.2",
                        "liquidity": "flexible",
                        "matures_at_unix_nanos": None,
                        "state": "active",
                        "accrued_rewards": [
                            {"asset": "ETH", "amount": "0.01", "component": None}
                        ],
                    }
                ],
                "outcomes": [],
                "errors": [{"segment": "staking", "message": "unauthorized"}],
            },
            title="manual-live-readonly · 账户运行结果",
        ),
        width=180,
    )

    for label in (
        "类型",
        "产品",
        "本金",
        "可赎回",
        "流动性",
        "累计奖励",
        "查询问题",
    ):
        assert label in content
    assert "eth-staking" in content
    assert "ETH 0.01" in content
    assert "staking：unauthorized" in content
    for raw_field in ("holdings", "outcomes", "errors"):
        assert raw_field not in content


def test_account_fees_are_rendered_as_rate_and_discount_tables() -> None:
    content = renderable_plain_text(
        account._account_result_renderable(
            "account.fees",
            {
                "account_id": "manual-live-readonly",
                "source": "direct_provider",
                "mode": "standalone",
                "product": "spot",
                "symbol": "BTCUSDT",
                "completeness": "complete",
                "maker": "0.001",
                "taker": "0.001",
                "buyer": None,
                "seller": None,
                "standard": {"maker": "0.001", "taker": "0.001"},
                "special": None,
                "tax": None,
                "discount": {
                    "enabled_for_account": True,
                    "enabled_for_symbol": True,
                    "asset": "BNB",
                    "rate": "0.25",
                },
                "rpi": None,
                "vip_tier": "VIP 1",
                "vip_tier_status": "included_in_observed_rate",
                "observed_at_unix_nanos": 123,
                "issues": ["费率已包含账户折扣"],
            },
            title="manual-live-readonly · 账户运行结果",
        ),
        width=180,
    )

    for label in (
        "费率字段",
        "交易对",
        "费率类别",
        "Maker",
        "Taker",
        "标准费率",
        "折扣字段",
        "账户已启用",
        "折扣资产",
        "说明",
    ):
        assert label in content
    assert "BTCUSDT" in content
    assert "VIP 1" in content
    assert "BNB" in content
    assert "费率已包含账户折扣" in content
    for raw_field in ("standard", "special", "discount", "issues"):
        assert raw_field not in content
