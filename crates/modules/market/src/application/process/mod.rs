//! Reusable Market process control and Conflux integration.

mod conflux;

pub(crate) use conflux::{
    MarketConfluxState, MarketSourceMode, MarketSourcePlan, ReferenceDemandConfig,
};
