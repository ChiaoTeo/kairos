use kairos_primitives::runtime::InstanceIdentity;
use kairos_reference_contract::{EncodeContext, ReferenceEncoder};

use crate::domain::{LifecycleEvent, ReferenceCatalog, ReferenceError, ReferenceResult};

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct EncodedPublication {
    pub event_id: String,
    pub sequence: u64,
    pub payload: Vec<u8>,
}

pub(crate) fn encode_publications(
    catalog: &ReferenceCatalog,
    events: &[LifecycleEvent],
    producer_incarnation: u64,
    identity: &InstanceIdentity,
) -> ReferenceResult<Vec<EncodedPublication>> {
    events
        .iter()
        .map(|event| encode_publication(catalog, event, producer_incarnation, identity))
        .collect()
}

fn encode_publication(
    catalog: &ReferenceCatalog,
    event: &LifecycleEvent,
    producer_incarnation: u64,
    identity: &InstanceIdentity,
) -> ReferenceResult<EncodedPublication> {
    let kind = event.record_kind.as_deref().ok_or_else(|| {
        ReferenceError::Publication("Reference event is missing record_kind".into())
    })?;
    let id = event.record_id.as_deref().ok_or_else(|| {
        ReferenceError::Publication("Reference event is missing record_id".into())
    })?;
    let sequence = event
        .event_id
        .rsplit(':')
        .next()
        .and_then(|value| value.parse::<u64>().ok())
        .ok_or_else(|| {
            ReferenceError::Publication(format!(
                "Reference event has an invalid sequence identity: {}",
                event.event_id
            ))
        })?;
    let context = EncodeContext::event(
        "reference-actor",
        producer_incarnation,
        identity.clone(),
        sequence,
        event.event_id.clone(),
        event.generation.get(),
    )
    .map_err(ReferenceError::Invalid)?;
    let updated = !event.event_type.ends_with("_added") && event.event_type != "listed";
    let occurred_at = event.event_time_unix_nanos.get();
    let payload = match kind {
        "exchange" => {
            let record = catalog.exchanges.get(id).ok_or_else(|| missing(kind, id))?;
            let record = contract_exchange(record);
            if updated {
                ReferenceEncoder::exchange_updated(&record, &context, occurred_at)
            } else {
                ReferenceEncoder::exchange_upserted(&record, &context, occurred_at)
            }
        },
        "asset" => {
            let record = catalog.assets.get(id).ok_or_else(|| missing(kind, id))?;
            let record = contract_asset(record);
            if updated {
                ReferenceEncoder::asset_updated(&record, &context, occurred_at)
            } else {
                ReferenceEncoder::asset_upserted(&record, &context, occurred_at)
            }
        },
        "instrument" => {
            let record = catalog
                .instruments
                .get(id)
                .ok_or_else(|| missing(kind, id))?;
            let record = contract_instrument(record);
            if updated {
                ReferenceEncoder::instrument_updated(&record, &context, occurred_at)
            } else {
                ReferenceEncoder::instrument_upserted(&record, &context, occurred_at)
            }
        },
        "listing" => {
            let record = catalog.listings.get(id).ok_or_else(|| missing(kind, id))?;
            let record = contract_listing(record);
            if updated {
                ReferenceEncoder::listing_updated(&record, &context, occurred_at)
            } else {
                ReferenceEncoder::listing_upserted(&record, &context, occurred_at)
            }
        },
        "market" => {
            let record = catalog.markets.get(id).ok_or_else(|| missing(kind, id))?;
            let record = contract_market(record);
            if updated {
                ReferenceEncoder::market_updated(&record, &context, occurred_at)
            } else {
                ReferenceEncoder::market_upserted(&record, &context, occurred_at)
            }
        },
        other => {
            return Err(ReferenceError::Publication(format!(
                "Reference v2 event schema is not defined for record kind {other}"
            )));
        },
    }
    .map_err(|error| ReferenceError::Publication(error.to_string()))?;
    Ok(EncodedPublication {
        event_id: event.event_id.clone(),
        sequence,
        payload,
    })
}

fn missing(kind: &str, id: &str) -> ReferenceError {
    ReferenceError::Publication(format!("Reference record missing: {kind}:{id}"))
}

fn contract_exchange(value: &crate::domain::Exchange) -> kairos_reference_contract::Exchange {
    kairos_reference_contract::Exchange {
        exchange_id: value.exchange_id.clone(),
        name: value.name.clone(),
        status: value.status,
    }
}

fn contract_asset(value: &crate::domain::Asset) -> kairos_reference_contract::Asset {
    kairos_reference_contract::Asset {
        asset_id: value.asset_id.clone(),
        code: value.code.clone(),
        name: value.name.clone(),
        asset_class: value.asset_class,
        status: value.status,
    }
}

fn contract_instrument(value: &crate::domain::Instrument) -> kairos_reference_contract::Instrument {
    kairos_reference_contract::Instrument {
        instrument_id: value.instrument_id.clone(),
        symbol: value.symbol.clone(),
        name: value.name.clone(),
        instrument_type: value.instrument_type,
        product_family: None,
        issuer_id: value.issuer_id.clone(),
        share_class: value.share_class.clone(),
        primary_currency_asset_id: value.primary_currency_asset_id.clone(),
        underlying_instrument_id: value.underlying_instrument_id.clone(),
        expiry_unix_nanos: value.expiry_unix_nanos,
        strike: value.strike.clone(),
        option_right: value.option_right.clone(),
        status: value.status,
    }
}

fn contract_listing(value: &crate::domain::Listing) -> kairos_reference_contract::Listing {
    kairos_reference_contract::Listing {
        listing_id: value.listing_id.clone(),
        instrument_id: value.instrument_id.clone(),
        exchange_id: value.exchange_id.clone(),
        exchange_symbol: value.exchange_symbol.clone(),
        status: value.status,
        effective_from_unix_nanos: value.effective_from_unix_nanos,
        effective_to_unix_nanos: value.effective_to_unix_nanos,
    }
}

fn contract_market(value: &crate::domain::Market) -> kairos_reference_contract::Market {
    kairos_reference_contract::Market {
        market_id: value.market_id.clone(),
        instrument_id: value.instrument_id.clone(),
        listing_id: value.listing_id.clone(),
        exchange_id: value.exchange_id.clone(),
        instrument_kind: value.instrument_kind,
        asset_type: value.asset_type,
        underlying_instrument_id: value.underlying_instrument_id.clone(),
        venue_symbol: value.venue_symbol.clone(),
        base_asset_id: value.base_asset_id.clone(),
        quote_asset_id: value.quote_asset_id.clone(),
        status: value.status,
        price_tick: value.price_tick.clone(),
        quantity_tick: value.quantity_tick.clone(),
        price_precision: value.price_precision,
        quantity_precision: value.quantity_precision,
        minimum_quantity: value.minimum_quantity.clone(),
        minimum_notional: value.minimum_notional.clone(),
        contract_size: value.contract_size.clone(),
        effective_from_unix_nanos: value.effective_from_unix_nanos,
        effective_to_unix_nanos: value.effective_to_unix_nanos,
    }
}
