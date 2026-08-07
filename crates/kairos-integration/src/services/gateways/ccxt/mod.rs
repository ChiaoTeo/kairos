//! CCXT-backed gateway family.

pub(crate) mod market;

pub(crate) use market::{CcxtMarketClient, CcxtReferenceConnection, CcxtReferenceFactory};
