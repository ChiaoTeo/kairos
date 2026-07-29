from __future__ import annotations

import kairospy.core.reference as reference
from kairospy.core.reference import BrokerId, ExchangeId, ProviderId
from kairospy.core.reference import brokers, exchanges, providers, resolve_broker, resolve_exchange, resolve_provider


def test_reference_participant_registries_are_core_owned() -> None:
    assert {str(item.exchange_id) for item in exchanges()} >= {"binance", "hyperliquid", "okx"}
    assert {str(item.broker_id) for item in brokers()} >= {"binance", "okx"}
    assert {str(item.provider_id) for item in providers()} >= {"massive"}
    assert isinstance(resolve_exchange("okex").exchange_id, ExchangeId)
    assert isinstance(resolve_broker("okex").broker_id, BrokerId)
    assert isinstance(resolve_provider("massive").provider_id, ProviderId)
    assert str(resolve_exchange("xnas").exchange_id) == "nasdaq"


def test_reference_core_does_not_export_import_builder_rules() -> None:
    assert not hasattr(reference, "InstrumentTemplate")
    assert not hasattr(reference, "EquityTemplate")
    assert not hasattr(reference, "instrument_rule_for_market_type")
    assert not hasattr(reference, "instrument_product_for_market")
