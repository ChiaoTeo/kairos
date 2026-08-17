//! Static and dynamic market-data subscription use cases.

mod dynamic_subscription;
mod lifecycle;
mod resolution;
mod static_subscription;

pub(crate) use resolution::{resolve_market, resolve_option_markets};
