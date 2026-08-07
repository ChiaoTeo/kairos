//! Massive market-data gateway family.

mod client;
mod market;
pub(crate) mod market_stream;

pub(crate) use client::MassiveStocksRestClient;
pub(crate) use market::MassiveReferenceConnection;
