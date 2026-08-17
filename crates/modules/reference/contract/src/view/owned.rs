use crate::{ContractError, ContractResult, ReferenceLatestSnapshot};
use kairos_protocol::generated::kairos::{common::v_2::Decimal64, reference::v_2 as fb};

pub fn decode_reference_latest(
    frame: &super::ReferenceViewFrame,
) -> ContractResult<ReferenceLatestSnapshot> {
    let view = frame.decode()?;
    let metadata = view.metadata();
    if metadata.generation() != frame.generation()
        || metadata.applied_revision().unwrap_or_default()
            != frame.envelope_metadata().applied_event_sequence
    {
        return Err(ContractError::Invalid(
            "Reference view envelope and payload watermarks differ".into(),
        ));
    }
    let state = view.state();
    Ok(ReferenceLatestSnapshot {
        actor_id: metadata.owner_id().to_owned(),
        workspace_id: metadata.workspace_id().to_owned(),
        launch_id: metadata.launch_id().map(str::to_owned),
        instance_id: metadata.instance_id().map(str::to_owned),
        generation: metadata.generation(),
        event_sequence: metadata.applied_revision().unwrap_or_default(),
        entities: state
            .entities()
            .iter()
            .map(|value| crate::Entity {
                entity_id: value.entity_id().to_owned(),
                entity_type: value.entity_type().to_owned(),
                name: value.name().to_owned(),
                status: lifecycle(value.status()),
            })
            .collect(),
        assets: state
            .assets()
            .iter()
            .map(|value| {
                Ok(crate::Asset {
                    asset_id: value.asset_id().to_owned(),
                    code: value.code().to_owned(),
                    name: value.name().map(str::to_owned),
                    asset_class: value
                        .asset_class()
                        .parse()
                        .map_err(|error| ContractError::Invalid(format!("{error}")))?,
                    status: lifecycle(value.status()),
                })
            })
            .collect::<ContractResult<Vec<_>>>()?,
        instruments: state
            .instruments()
            .iter()
            .map(|value| {
                Ok(crate::Instrument {
                    instrument_id: value.instrument_id().to_owned(),
                    symbol: value.symbol().to_owned(),
                    name: value.name().map(str::to_owned),
                    instrument_type: value
                        .instrument_type()
                        .parse()
                        .map_err(|error| ContractError::Invalid(format!("{error}")))?,
                    product_family: value.product_family().map(str::to_owned),
                    issuer_id: value.issuer_id().map(str::to_owned),
                    share_class: value.share_class().map(str::to_owned),
                    primary_currency_asset_id: value.primary_currency_asset_id().map(str::to_owned),
                    underlying_instrument_id: value.underlying_instrument_id().map(str::to_owned),
                    expiry_unix_nanos: nonzero(value.expiry_unix_nanos()),
                    strike: decimal(value.strike()),
                    option_right: value.option_right().map(str::to_owned),
                    status: lifecycle(value.status()),
                })
            })
            .collect::<ContractResult<Vec<_>>>()?,
        listings: state
            .listings()
            .iter()
            .map(|value| crate::Listing {
                listing_id: value.listing_id().to_owned(),
                instrument_id: value.instrument_id().to_owned(),
                exchange_id: value.exchange_id().to_owned(),
                exchange_symbol: value.exchange_symbol().to_owned(),
                status: lifecycle(value.status()),
                effective_from_unix_nanos: value.effective_from_unix_nanos(),
                effective_to_unix_nanos: nonzero(value.effective_to_unix_nanos()),
            })
            .collect(),
        markets: state
            .markets()
            .iter()
            .map(|value| {
                Ok(crate::Market {
                    market_id: value.market_id().to_owned(),
                    market_key: value.market_key().to_owned(),
                    instrument_id: value.instrument_id().to_owned(),
                    listing_id: value.listing_id().to_owned(),
                    exchange_id: value.exchange_id().to_owned(),
                    market_type: kairos_primitives::ProviderProductCode::new(value.market_type())
                        .map_err(|error| ContractError::Invalid(error.to_string()))?,
                    asset_type: value
                        .asset_type()
                        .map(str::parse)
                        .transpose()
                        .map_err(|error| ContractError::Invalid(format!("{error}")))?,
                    underlying_instrument_id: value.underlying_instrument_id().map(str::to_owned),
                    source_symbol: value.source_symbol().to_owned(),
                    base_asset_id: value.base_asset_id().map(str::to_owned),
                    quote_asset_id: value.quote_asset_id().map(str::to_owned),
                    status: lifecycle(value.status()),
                    price_tick: decimal(value.price_tick()),
                    quantity_tick: decimal(value.quantity_tick()),
                    price_precision: value.price_precision(),
                    quantity_precision: value.quantity_precision(),
                    minimum_quantity: decimal(value.minimum_quantity()),
                    minimum_notional: decimal(value.minimum_notional()),
                    contract_size: decimal(value.contract_size()),
                    effective_from_unix_nanos: value.effective_from_unix_nanos(),
                    effective_to_unix_nanos: nonzero(value.effective_to_unix_nanos()),
                })
            })
            .collect::<ContractResult<Vec<_>>>()?,
        financial_products: state
            .financial_products()
            .iter()
            .map(|value| crate::FinancialProduct {
                product_id: value.product_id().to_owned(),
                product_type: value.product_type().to_owned(),
                name: value.name().to_owned(),
                asset_id: value.asset_id().to_owned(),
                provider_product_id: value.provider_product_id().to_owned(),
                provider_id: value.provider_id().map(str::to_owned),
                issuer_id: value.issuer_id().map(str::to_owned),
                currency_asset_id: value.currency_asset_id().map(str::to_owned),
                min_amount: decimal(value.min_amount()),
                max_amount: decimal(value.max_amount()),
                apr: decimal(value.apr()),
                lock_period_days: value.lock_period_days(),
                maturity_at_unix_nanos: nonzero(value.maturity_at_unix_nanos()),
                status: lifecycle(value.status()),
                effective_from_unix_nanos: value.effective_from_unix_nanos(),
                effective_to_unix_nanos: nonzero(value.effective_to_unix_nanos()),
            })
            .collect(),
        execution_accesses: state
            .execution_accesses()
            .iter()
            .map(|value| crate::ExecutionAccess {
                access_id: value.access_id().to_owned(),
                routing_mode: value.routing_mode().unwrap_or_default().to_owned(),
                instrument_id: value.instrument_id().map(str::to_owned),
                listing_id: value.listing_id().map(str::to_owned),
                market_id: value.market_id().map(str::to_owned),
                destination_market_id: value.destination_market_id().map(str::to_owned),
                broker_id: value.broker_id().map(str::to_owned),
                provider_id: value.provider_id().to_owned(),
                provider_product: value.product_family().to_owned(),
                provider_symbol: value.provider_symbol().to_owned(),
                settlement_asset_id: value.settlement_asset_id().map(str::to_owned),
                status: lifecycle(value.status()),
                effective_from_unix_nanos: value.effective_from_unix_nanos(),
                effective_to_unix_nanos: nonzero(value.effective_to_unix_nanos()),
            })
            .collect(),
        market_data_accesses: state
            .market_data_accesses()
            .iter()
            .map(|value| crate::MarketDataAccess {
                access_id: value.access_id().to_owned(),
                market_id: value.market_id().to_owned(),
                provider_id: value.provider_id().to_owned(),
                provider_product: value.product_family().to_owned(),
                provider_symbol: value.provider_symbol().to_owned(),
                status: lifecycle(value.status()),
                effective_from_unix_nanos: value.effective_from_unix_nanos(),
                effective_to_unix_nanos: nonzero(value.effective_to_unix_nanos()),
            })
            .collect(),
        provider_health: state
            .provider_health()
            .iter()
            .map(|value| crate::ProviderHealthState {
                provider_id: value.provider_id().to_owned(),
                status: value.status().to_owned(),
                message: value.message().map(str::to_owned),
                updated_at_unix_nanos: value.updated_at_unix_nanos(),
            })
            .collect(),
        option_underlyings: state
            .option_underlyings()
            .iter()
            .map(str::to_owned)
            .collect(),
        lifecycle_events: state
            .lifecycle_events()
            .iter()
            .map(|value| crate::LifecycleEntry {
                event_id: value.event_id().to_owned(),
                event_type: value.event_type().to_owned(),
                event_time_unix_nanos: value.event_time_unix_nanos(),
                record_kind: value.record_kind().map(str::to_owned),
                record_id: value.record_id().map(str::to_owned),
            })
            .collect(),
    })
}

fn lifecycle(value: fb::ReferenceLifecycleStatus) -> String {
    value
        .variant_name()
        .unwrap_or("UNSPECIFIED")
        .to_ascii_lowercase()
}

fn nonzero(value: u64) -> Option<u64> {
    (value != 0).then_some(value)
}

fn decimal(value: Option<&Decimal64>) -> Option<String> {
    value.map(|value| {
        let scale = value.scale() as usize;
        let negative = value.mantissa() < 0;
        let digits = i128::from(value.mantissa()).abs().to_string();
        if scale == 0 {
            return format!("{}{digits}", if negative { "-" } else { "" });
        }
        let padded = format!("{:0>width$}", digits, width = scale + 1);
        let split = padded.len() - scale;
        format!(
            "{}{}.{}",
            if negative { "-" } else { "" },
            &padded[..split],
            &padded[split..]
        )
    })
}
