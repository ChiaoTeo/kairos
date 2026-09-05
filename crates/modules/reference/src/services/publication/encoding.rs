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

pub(crate) fn encode_coverage_publications(
    transitions: &[(
        kairos_reference_contract::CoverageState,
        kairos_reference_contract::ReferenceCoverage,
    )],
    producer_incarnation: u64,
    identity: &InstanceIdentity,
) -> ReferenceResult<Vec<EncodedPublication>> {
    transitions
        .iter()
        .map(|(previous_state, coverage)| {
            let sequence = coverage
                .event_sequence
                .ok_or_else(|| {
                    ReferenceError::Publication(
                        "coverage transition is missing event_sequence".into(),
                    )
                })?
                .get();
            let event_id = format!("reference:{sequence:020}");
            let context = EncodeContext::event(
                "reference-actor",
                producer_incarnation,
                identity.clone(),
                sequence,
                event_id.clone(),
                coverage.generation.unwrap_or_default().get(),
            )
            .map_err(ReferenceError::Invalid)?;
            let payload = ReferenceEncoder::coverage_state_changed(
                coverage,
                *previous_state,
                &context,
                coverage.last_attempt_unix_nanos.unwrap_or_default().get(),
            )
            .map_err(|error| ReferenceError::Publication(error.to_string()))?;
            Ok(EncodedPublication {
                event_id,
                sequence,
                payload,
            })
        })
        .collect()
}

pub(crate) fn encode_publication(
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
        "venue" => {
            let record = catalog.venues.get(id).ok_or_else(|| missing(kind, id))?;
            let record = contract_venue(record);
            if updated {
                ReferenceEncoder::venue_updated(&record, &context, occurred_at)
            } else {
                ReferenceEncoder::venue_upserted(&record, &context, occurred_at)
            }
        },
        "venue_listing" => {
            let record = catalog
                .venue_listings
                .get(id)
                .ok_or_else(|| missing(kind, id))?;
            let record = contract_venue_listing(record);
            if updated {
                ReferenceEncoder::venue_listing_updated(&record, &context, occurred_at)
            } else {
                ReferenceEncoder::venue_listing_upserted(&record, &context, occurred_at)
            }
        },
        "venue_market" => {
            let record = catalog
                .venue_markets
                .get(id)
                .ok_or_else(|| missing(kind, id))?;
            let record = contract_venue_market(record);
            if updated {
                ReferenceEncoder::venue_market_updated(&record, &context, occurred_at)
            } else {
                ReferenceEncoder::venue_market_upserted(&record, &context, occurred_at)
            }
        },
        "provider_catalog_membership" => {
            let (source_id, instrument_id) = id.split_once('|').ok_or_else(|| {
                ReferenceError::Publication(format!(
                    "Reference membership event has invalid identity: {id}"
                ))
            })?;
            let key = (
                kairos_primitives::reference::ReferenceSourceId::new(source_id)
                    .map_err(|error| ReferenceError::Invalid(error.to_string()))?,
                kairos_primitives::reference::InstrumentId::new(instrument_id)
                    .map_err(|error| ReferenceError::Invalid(error.to_string()))?,
            );
            let record = catalog
                .provider_catalog_memberships
                .get(&key)
                .ok_or_else(|| missing(kind, id))?;
            let record = contract_provider_catalog_membership(record);
            if updated {
                ReferenceEncoder::provider_catalog_membership_updated(
                    &record,
                    &context,
                    occurred_at,
                )
            } else {
                ReferenceEncoder::provider_catalog_membership_upserted(
                    &record,
                    &context,
                    occurred_at,
                )
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
        settlement_asset_id: value.settlement_asset_id.clone(),
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

fn contract_venue(value: &crate::domain::Venue) -> kairos_reference_contract::Venue {
    kairos_reference_contract::Venue {
        venue_id: value.venue_id.clone(),
        name: value.name.clone(),
        venue_kind: match value.venue_kind {
            crate::domain::VenueKind::RegulatedExchange => {
                kairos_reference_contract::VenueKind::RegulatedExchange
            },
            crate::domain::VenueKind::RegulatedMarket => {
                kairos_reference_contract::VenueKind::RegulatedMarket
            },
            crate::domain::VenueKind::TradingPlatform => {
                kairos_reference_contract::VenueKind::TradingPlatform
            },
            crate::domain::VenueKind::Ats => kairos_reference_contract::VenueKind::Ats,
            crate::domain::VenueKind::Pts => kairos_reference_contract::VenueKind::Pts,
            crate::domain::VenueKind::OtcFacility => {
                kairos_reference_contract::VenueKind::OtcFacility
            },
            crate::domain::VenueKind::Dealer => kairos_reference_contract::VenueKind::Dealer,
            crate::domain::VenueKind::TradeReportingFacility => {
                kairos_reference_contract::VenueKind::TradeReportingFacility
            },
            crate::domain::VenueKind::Unknown => kairos_reference_contract::VenueKind::Unknown,
        },
        roles: value
            .roles
            .iter()
            .map(|role| match role {
                crate::domain::VenueRole::Listing => kairos_reference_contract::VenueRole::Listing,
                crate::domain::VenueRole::Execution => {
                    kairos_reference_contract::VenueRole::Execution
                },
                crate::domain::VenueRole::Reporting => {
                    kairos_reference_contract::VenueRole::Reporting
                },
            })
            .collect(),
        mic: value.mic.clone(),
        operating_mic: value.operating_mic.clone(),
        parent_venue_id: value.parent_venue_id.clone(),
        jurisdiction: value.jurisdiction.clone(),
        status: value.status,
    }
}

fn contract_venue_listing(
    value: &crate::domain::VenueListing,
) -> kairos_reference_contract::VenueListing {
    kairos_reference_contract::VenueListing {
        listing_id: value.listing_id.clone(),
        instrument_id: value.instrument_id.clone(),
        listing_venue_id: value.listing_venue_id.clone(),
        market_segment_id: value.market_segment_id.clone(),
        listing_symbol: value.listing_symbol.clone(),
        listing_role: match value.listing_role {
            crate::domain::ListingRole::Primary => kairos_reference_contract::ListingRole::Primary,
            crate::domain::ListingRole::Secondary => {
                kairos_reference_contract::ListingRole::Secondary
            },
            crate::domain::ListingRole::CrossListing => {
                kairos_reference_contract::ListingRole::CrossListing
            },
            crate::domain::ListingRole::AdmissionWithoutPrimaryDesignation => {
                kairos_reference_contract::ListingRole::AdmissionWithoutPrimaryDesignation
            },
            crate::domain::ListingRole::Unknown => kairos_reference_contract::ListingRole::Unknown,
        },
        status: value.status,
        effective_from_unix_nanos: value.effective_from_unix_nanos,
        effective_to_unix_nanos: value.effective_to_unix_nanos,
    }
}

fn contract_venue_market(
    value: &crate::domain::VenueMarket,
) -> kairos_reference_contract::VenueMarket {
    kairos_reference_contract::VenueMarket {
        market_id: value.market_id.clone(),
        instrument_id: value.instrument_id.clone(),
        execution_venue_id: value.execution_venue_id.clone(),
        origin_listing_id: value.origin_listing_id.clone(),
        market_segment_id: value.market_segment_id.clone(),
        venue_symbol: value.venue_symbol.clone(),
        trading_calendar_id: value.trading_calendar_id.clone(),
        trading_session_ids: value.trading_session_ids.clone(),
        base_asset_id: value.base_asset_id.clone(),
        quote_asset_id: value.quote_asset_id.clone(),
        status: value.status,
        trading_rules: kairos_reference_contract::TradingRules {
            price_tick: value.trading_rules.price_tick,
            quantity_tick: value.trading_rules.quantity_tick,
            price_precision: value.trading_rules.price_precision,
            quantity_precision: value.trading_rules.quantity_precision,
            minimum_quantity: value.trading_rules.minimum_quantity,
            minimum_notional: value.trading_rules.minimum_notional,
            contract_size: value.trading_rules.contract_size,
        },
        effective_from_unix_nanos: value.effective_from_unix_nanos,
        effective_to_unix_nanos: value.effective_to_unix_nanos,
    }
}

fn contract_provider_catalog_membership(
    value: &crate::domain::ProviderCatalogMembership,
) -> kairos_reference_contract::ProviderCatalogMembership {
    kairos_reference_contract::ProviderCatalogMembership {
        source_id: value.source_id.clone(),
        instrument_id: value.instrument_id.clone(),
        provider_symbol: value.provider_symbol.clone(),
        provider_product: value.provider_product.clone(),
        status: value.status,
        effective_from_unix_nanos: value.effective_from_unix_nanos,
        effective_to_unix_nanos: value.effective_to_unix_nanos,
    }
}
