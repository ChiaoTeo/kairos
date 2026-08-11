//! Binance endpoint vocabulary and provider connections without business entities.

pub(crate) mod account_events;
pub(crate) mod async_margin_account_events;
pub(crate) mod async_margin_order_events;
pub(crate) mod equity;
pub(crate) mod funding;
pub(crate) mod futures;
pub(crate) mod margin;
pub(crate) mod margin_order;
pub(crate) mod margin_query;
pub(crate) mod market_data;
pub(crate) mod options;
pub(crate) mod order_query;
pub(crate) mod signing;
pub(crate) mod spot;

pub(crate) use crate::application::participants::binance::types::ConnectionDomain;

pub(crate) fn descriptor(
    binding_id: impl Into<String>,
    domain: impl Into<String>,
) -> Result<crate::domain::ConnectionDescriptor, String> {
    crate::domain::ConnectionDescriptor::new(
        binding_id,
        crate::domain::ParticipantRef::new(crate::domain::ParticipantKind::Exchange, "binance")?,
        domain,
    )
}
