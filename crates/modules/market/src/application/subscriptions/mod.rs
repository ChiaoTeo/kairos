//! Static and dynamic market-data subscription use cases.

mod dynamic_subscription;
mod lifecycle;
mod resolution;
mod static_subscription;

pub(crate) use resolution::{
    OptionSelectionFilter, resolve_market, resolve_market_by_id, resolve_option_markets,
};
