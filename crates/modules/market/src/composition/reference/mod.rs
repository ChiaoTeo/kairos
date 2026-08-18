//! Concrete consumption of the external Reference contract.

mod projection;

pub(crate) use projection::{
    adapter_observation_capabilities, build_reference_projection, project_market_universe,
};
