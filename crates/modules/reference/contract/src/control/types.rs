use kairos_primitives::{
    AssetClass, AssetId, Exchange, InstrumentId, InstrumentKind, IssuerId, ListingId, Price,
    ReferenceStatus, Symbol, UnixNanos,
};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct UpsertAssetRequest {
    pub asset_id: AssetId,
    pub code: Symbol,
    pub name: Option<String>,
    pub asset_class: AssetClass,
    pub status: ReferenceStatus,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct UpsertInstrumentRequest {
    pub instrument_id: InstrumentId,
    pub symbol: Symbol,
    pub name: Option<String>,
    pub instrument_type: InstrumentKind,
    #[serde(default)]
    pub issuer_id: Option<IssuerId>,
    #[serde(default)]
    pub share_class: Option<String>,
    #[serde(default)]
    pub primary_currency_asset_id: Option<AssetId>,
    pub underlying_instrument_id: Option<InstrumentId>,
    pub expiry_unix_nanos: Option<UnixNanos>,
    pub strike: Option<Price>,
    pub option_right: Option<String>,
    pub status: ReferenceStatus,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct UpsertListingRequest {
    pub listing_id: ListingId,
    pub instrument_id: InstrumentId,
    pub exchange_id: Exchange,
    pub exchange_symbol: Symbol,
    pub status: ReferenceStatus,
    pub effective_from_unix_nanos: UnixNanos,
    pub effective_to_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReferenceSourceControlRequest {
    pub source_id: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReferenceOptionCoverageRequest {
    pub underlying: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferenceControlError {
    pub code: String,
    pub message: String,
    #[serde(default)]
    pub retryable: bool,
    #[serde(default)]
    pub details: std::collections::BTreeMap<String, serde_json::Value>,
}

/// Closed REST command/query set exposed by the Reference process.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub enum ReferenceRestRequest {
    Health,
    Refresh { source_id: Option<String> },
    Publish,
    PauseSource(ReferenceSourceControlRequest),
    ResumeSource(ReferenceSourceControlRequest),
    AddOptionCoverage(ReferenceOptionCoverageRequest),
    RemoveOptionCoverage(ReferenceOptionCoverageRequest),
    UpsertAsset(UpsertAssetRequest),
    UpsertInstrument(UpsertInstrumentRequest),
    UpsertListing(UpsertListingRequest),
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferenceHealthResponse {
    pub status: String,
    pub providers: Vec<ReferenceProviderHealth>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferenceProviderHealth {
    pub source_id: String,
    pub status: String,
    pub stale: bool,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferenceRefreshResponse {
    pub generation: u64,
    pub event_sequence: u64,
    pub changed: bool,
    pub change_count: u64,
    pub publication_pending: bool,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferencePublishResponse {
    pub generation: u64,
    pub events: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferenceMutationResponse {
    pub generation: u64,
    pub events: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferenceSourceStatusResponse {
    pub source_id: String,
    pub status: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferenceOptionCoverageResponse {
    pub underlying: String,
    pub enabled: bool,
    pub underlyings: Vec<String>,
    pub generation: u64,
    pub event_sequence: u64,
    pub changed: bool,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub enum ReferenceRestResponse {
    Health(Result<ReferenceHealthResponse, ReferenceControlError>),
    Refresh(Result<ReferenceRefreshResponse, ReferenceControlError>),
    Publish(Result<ReferencePublishResponse, ReferenceControlError>),
    PauseSource(Result<ReferenceSourceStatusResponse, ReferenceControlError>),
    ResumeSource(Result<ReferenceSourceStatusResponse, ReferenceControlError>),
    AddOptionCoverage(Result<ReferenceOptionCoverageResponse, ReferenceControlError>),
    RemoveOptionCoverage(Result<ReferenceOptionCoverageResponse, ReferenceControlError>),
    UpsertAsset(Result<ReferenceMutationResponse, ReferenceControlError>),
    UpsertInstrument(Result<ReferenceMutationResponse, ReferenceControlError>),
    UpsertListing(Result<ReferenceMutationResponse, ReferenceControlError>),
}

#[cfg(test)]
mod tests {
    use kairos_primitives::{AssetClass, AssetId, ReferenceStatus, Symbol};

    use super::UpsertAssetRequest;

    #[test]
    fn administrative_mutation_is_owned_by_the_reference_contract() {
        let request = UpsertAssetRequest {
            asset_id: AssetId::new("asset:btc").unwrap(),
            code: Symbol::new("BTC").unwrap(),
            name: Some("Bitcoin".into()),
            asset_class: AssetClass::Crypto,
            status: ReferenceStatus::Active,
        };
        let value = serde_json::to_value(request).unwrap();
        assert_eq!(value["asset_id"], "asset:btc");
        assert_eq!(value["asset_class"], "crypto");
        assert_eq!(value["status"], "active");
    }
}
