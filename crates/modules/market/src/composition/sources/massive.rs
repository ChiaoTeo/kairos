use std::path::Path;

use super::super::config::{MarketSourceBinding, MassiveMarketProduct};
use kairos_integration::application::credential::load_workspace_credential;

use crate::MarketApplication;

use super::super::{attach_massive_source_with_id, default_endpoint, MarketProduct};

pub(super) fn attach(
    application: &mut MarketApplication,
    credentials_root: &Path,
    source_id: &str,
    binding: &MarketSourceBinding,
) -> Result<(), String> {
    let MarketSourceBinding::Massive {
        product,
        credential_id,
        endpoint,
        ..
    } = binding
    else {
        return Err(format!(
            "Market source {source_id} is not a Massive binding"
        ));
    };
    let credential =
        load_workspace_credential(credentials_root, "massive", Some(credential_id))?
            .ok_or_else(|| format!("market source {source_id} requires a Massive credential"))?;
    let (product, market_type, endpoint_key) = match product {
        MassiveMarketProduct::Equity => {
            (MarketProduct::Equity, "equity", "massive-equity-websocket")
        }
        MassiveMarketProduct::Options => (
            MarketProduct::Options,
            "options",
            "massive-options-websocket",
        ),
    };
    attach_massive_source_with_id(
        application,
        source_id,
        market_type,
        "equity",
        product,
        credential.api_key,
        endpoint
            .clone()
            .unwrap_or_else(|| default_endpoint(endpoint_key).to_owned()),
    )
}
