from kairospy.domain.account import AccountModel, ProductFamily as AccountProductFamily, ExternalAccountIdentity, account_segment_from_name
from kairospy.domain.reference import AssetType, MarketRef
from kairospy.infrastructure.integrations.domain import ProductFamily as IntegrationProductFamily
from kairospy.application.usecases.account.domain.segments import default_account_segments


def test_product_family_has_one_shared_definition() -> None:
    assert IntegrationProductFamily is AccountProductFamily
    assert IntegrationProductFamily.USD_M_FUTURES.value == "usd_m_futures"
    assert IntegrationProductFamily.COIN_M_FUTURES.value == "coin_m_futures"


def test_equity_account_alias_resolves_to_spot_account_product() -> None:
    segment = account_segment_from_name(ExternalAccountIdentity("ibkr", "paper"), "equity")
    assert segment.segment_id == "spot"
    assert segment.product_family is AccountProductFamily.SPOT
    assert segment.model is AccountModel.NO_MARGIN


def test_market_reference_separates_asset_type_from_product_family() -> None:
    equity = MarketRef.ephemeral(venue="nasdaq", market="equity", source_symbol="AAPL")
    futures = MarketRef.ephemeral(venue="binance", market="usd_m_futures", source_symbol="BTC/USDT")

    assert equity.asset_type is AssetType.EQUITY
    assert equity.product_family is AccountProductFamily.SPOT
    assert futures.asset_type is AssetType.CRYPTO
    assert futures.product_family is AccountProductFamily.USD_M_FUTURES


def test_default_account_segments_contain_trading_products_not_services() -> None:
    segments = default_account_segments("binance")
    assert segments == ("spot", "cross_margin", "isolated_margin", "usd_m_futures", "coin_m_futures", "options")
