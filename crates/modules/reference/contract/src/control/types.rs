use kairos_primitives::decimal::Price;
use kairos_primitives::integration::ProviderId;
use kairos_primitives::reference::{
    AssetClass, AssetId, Exchange, InstrumentId, InstrumentKind, IssuerId, ListingId,
    ReferenceStatus, Symbol,
};
use kairos_primitives::time::{Generation, Sequence, UnixNanos};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct UpsertAssetRequest {
    pub asset_id: AssetId,
    pub code: Symbol,
    pub name: Option<String>,
    pub asset_class: AssetClass,
    pub status: ReferenceStatus,
    #[serde(default)]
    pub provenance: ReferenceUpsertProvenance,
    #[serde(default)]
    pub conflict_policy: ReferenceUpsertConflictPolicy,
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
    #[serde(default)]
    pub provenance: ReferenceUpsertProvenance,
    #[serde(default)]
    pub conflict_policy: ReferenceUpsertConflictPolicy,
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
    #[serde(default)]
    pub provenance: ReferenceUpsertProvenance,
    #[serde(default)]
    pub conflict_policy: ReferenceUpsertConflictPolicy,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ReferenceUpsertProvenance {
    #[default]
    Manual,
    Curated,
}

impl ReferenceUpsertProvenance {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Manual => "manual",
            Self::Curated => "curated",
        }
    }
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ReferenceUpsertConflictPolicy {
    #[default]
    RejectProviderOwned,
    AllowOverwrite,
}

impl ReferenceUpsertConflictPolicy {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::RejectProviderOwned => "reject_provider_owned",
            Self::AllowOverwrite => "allow_overwrite",
        }
    }

    pub const fn rejects_provider_owned(self) -> bool {
        matches!(self, Self::RejectProviderOwned)
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReferenceSourceControlRequest {
    pub source_id: ProviderId,
    pub desired_state: ReferenceSourceDesiredState,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReferenceSourceDefinitionRequest {
    pub source_id: ProviderId,
    pub provider_id: ProviderId,
    #[serde(default)]
    pub provider_product: Option<ReferenceProviderProduct>,
    #[serde(default)]
    pub scope: ReferenceSourceScope,
    pub desired_state: ReferenceSourceDesiredState,
    #[serde(default)]
    pub credential_binding: Option<String>,
    pub sync_policy: ReferenceSourceSyncPolicy,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReferenceSourceScope {
    pub kind: ReferenceSourceScopeKind,
    #[serde(default)]
    pub id: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReferenceOptionCoverageRequest {
    pub underlying: InstrumentId,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReferenceSourceScopeRequest {
    pub source_id: ProviderId,
    pub scope: ReferenceSourceScope,
    pub enabled: bool,
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

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferenceHealthResponse {
    pub status: ReferenceHealthStatus,
    pub providers: Vec<ReferenceProviderHealth>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferenceRuntimeStatusResponse {
    pub status: ReferenceRuntimeStatus,
    pub app_runtime: ReferenceAppRuntimeStatus,
    pub catalog: ReferenceCatalogRuntimeStatus,
    pub sources: Vec<ReferenceSourceRuntimeStatus>,
    #[serde(default)]
    pub coverage: ReferenceCoverageRuntimeStatus,
    pub publication: ReferencePublicationRuntimeStatus,
    pub diagnostics: Vec<ReferenceDiagnostic>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferenceAppRuntimeStatus {
    pub phase: ReferenceAppPhase,
    pub actor_id: String,
    pub source_id: String,
    pub refresh_interval_millis: u64,
    #[serde(default)]
    pub last_tick_started_unix_nanos: Option<UnixNanos>,
    #[serde(default)]
    pub last_tick_finished_unix_nanos: Option<UnixNanos>,
    #[serde(default)]
    pub last_tick_duration_millis: Option<u64>,
    #[serde(default)]
    pub next_tick_due_unix_nanos: Option<UnixNanos>,
    #[serde(default)]
    pub active_work_item_count: u32,
    #[serde(default)]
    pub queued_work_item_count: u32,
    #[serde(default)]
    pub last_error: Option<ReferenceAppRuntimeError>,
    #[serde(default)]
    pub tick_budget: ReferenceSourceTickBudget,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferenceAppRuntimeError {
    pub code: String,
    pub retryable: bool,
    pub message: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferenceSourceTickBudget {
    pub max_sources_per_tick: u32,
    pub max_batches_per_source: u32,
    #[serde(default)]
    pub max_records_per_batch: Option<u64>,
    #[serde(default)]
    pub max_wall_clock_millis: Option<u64>,
    #[serde(default)]
    pub max_publications_per_tick: Option<u32>,
}

impl Default for ReferenceSourceTickBudget {
    fn default() -> Self {
        Self {
            max_sources_per_tick: 1,
            max_batches_per_source: 1,
            max_records_per_batch: None,
            max_wall_clock_millis: None,
            max_publications_per_tick: None,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferenceCatalogRuntimeStatus {
    pub readiness: ReferenceCatalogReadiness,
    pub generation: Generation,
    pub event_sequence: Sequence,
    #[serde(default)]
    pub committed_at_unix_nanos: UnixNanos,
    #[serde(default)]
    pub entity_count: u64,
    #[serde(default)]
    pub asset_count: u64,
    #[serde(default)]
    pub instrument_count: u64,
    #[serde(default)]
    pub listing_count: u64,
    pub market_count: u64,
    #[serde(default)]
    pub active_market_count: u64,
    #[serde(default)]
    pub lifecycle_event_count: u64,
    #[serde(default)]
    pub integrity: ReferenceCatalogIntegrityStatus,
}

#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferenceCatalogIntegrityStatus {
    #[serde(default)]
    pub degraded: bool,
    #[serde(default)]
    pub missing_equity_market_count: u64,
    #[serde(default)]
    pub legacy_exchange_market_id_count: u64,
    #[serde(default)]
    pub legacy_exchange_listing_id_count: u64,
    #[serde(default)]
    pub option_listing_count: u64,
    #[serde(default)]
    pub option_market_count: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferencePublicationRuntimeStatus {
    pub pending_publication_count: u64,
    #[serde(default)]
    pub backlog_degraded: bool,
    #[serde(default)]
    pub batch_limit: u64,
    #[serde(default)]
    pub oldest_pending_event_id: Option<String>,
    #[serde(default)]
    pub last_error: Option<ReferencePublicationRuntimeError>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferencePublicationRuntimeError {
    pub code: String,
    pub retryable: bool,
    pub message: String,
}

#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferenceCoverageRuntimeStatus {
    pub option_underlyings: Vec<InstrumentId>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferenceSourceRuntimeStatus {
    pub source_id: ProviderId,
    #[serde(default)]
    pub provider_id: Option<ProviderId>,
    #[serde(default)]
    pub provider_product: Option<ReferenceProviderProduct>,
    #[serde(default)]
    pub source_kind: ReferenceSourceKind,
    #[serde(default)]
    pub configured: bool,
    #[serde(default)]
    pub enabled: bool,
    #[serde(default)]
    pub paused: bool,
    #[serde(default)]
    pub desired_state: Option<ReferenceSourceDesiredState>,
    #[serde(default)]
    pub sync_policy: Option<ReferenceSourceSyncPolicy>,
    #[serde(default)]
    pub scope: Option<ReferenceSourceScope>,
    #[serde(default)]
    pub credential_binding_present: Option<bool>,
    pub phase: ReferenceSourcePhase,
    pub progress: ReferenceSourceProgress,
    #[serde(default)]
    pub work_item: ReferenceSourceWorkItem,
    pub last_attempt_unix_nanos: Option<UnixNanos>,
    pub last_success_unix_nanos: Option<UnixNanos>,
    #[serde(default)]
    pub retry_after_unix_nanos: Option<UnixNanos>,
    #[serde(default)]
    pub retry_backoff_seconds: Option<u64>,
    pub consecutive_failures: u32,
    pub stale: bool,
    pub has_last_known_good: bool,
    #[serde(default)]
    pub last_error: Option<ReferenceSourceRuntimeError>,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReferenceSourceKind {
    #[default]
    Unknown,
    Global,
    Scoped,
    ManualCurated,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferenceSourceRuntimeError {
    pub code: String,
    pub retryable: bool,
    #[serde(default)]
    pub record_kind: Option<String>,
    #[serde(default)]
    pub record_id: Option<String>,
    pub message: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReferenceProviderProduct {
    Spot,
    Equity,
    Options,
    Usdm,
    Coinm,
    Margin,
    Swap,
    Futures,
    Perpetual,
    Unknown,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReferenceSourceDesiredState {
    Enabled,
    Disabled,
    Paused,
    Removed,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReferenceSourceSyncPolicy {
    FullSnapshot,
    PagedSnapshot,
    ScopedSnapshot,
    IncrementalDelta,
    ManualCurated,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReferenceSourceScopeKind {
    #[default]
    Global,
    ProviderCatalog,
    UnderlyingInstrument,
    Coverage,
    Custom,
}

impl ReferenceSourceScopeKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Global => "global",
            Self::ProviderCatalog => "provider_catalog",
            Self::UnderlyingInstrument => "underlying_instrument",
            Self::Coverage => "coverage",
            Self::Custom => "custom",
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferenceSourceProgress {
    pub kind: ReferenceSourceProgressKind,
    pub pages_done: Option<u64>,
    #[serde(default)]
    pub pages_total: Option<u64>,
    #[serde(default)]
    pub records_seen: Option<u64>,
    #[serde(default)]
    pub records_changed: Option<u64>,
    #[serde(default)]
    pub scope_id: Option<String>,
    #[serde(default)]
    pub scope_kind: Option<String>,
    #[serde(default)]
    pub cursor_present: Option<bool>,
}

#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferenceSourceWorkItem {
    pub work_item_id: Option<String>,
    pub scope_id: Option<String>,
    pub scope_kind: Option<String>,
    pub cursor_present: Option<bool>,
    #[serde(default)]
    pub skip_reason: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferenceDiagnostic {
    pub severity: ReferenceDiagnosticSeverity,
    pub code: String,
    pub message: String,
    pub next_action: Option<String>,
    #[serde(default)]
    pub source_id: Option<ProviderId>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferenceProviderHealth {
    pub source_id: ProviderId,
    pub status: ReferenceProviderStatus,
    pub stale: bool,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferenceRefreshResponse {
    pub generation: Generation,
    pub event_sequence: Sequence,
    pub changed: bool,
    pub change_count: u64,
    pub publication_pending: bool,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferencePublishResponse {
    pub generation: Generation,
    pub events: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferenceMutationResponse {
    pub generation: Generation,
    pub events: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferenceSourceStatusResponse {
    pub source_id: ProviderId,
    pub status: ReferenceProviderStatus,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferenceOptionCoverageResponse {
    pub underlying: InstrumentId,
    pub enabled: bool,
    pub underlyings: Vec<InstrumentId>,
    pub generation: Generation,
    pub event_sequence: Sequence,
    pub changed: bool,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ReferenceSourceScopeResponse {
    pub source_id: ProviderId,
    pub scope: ReferenceSourceScope,
    pub enabled: bool,
    pub generation: Generation,
    pub event_sequence: Sequence,
    pub changed: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReferenceHealthStatus {
    Ready,
    Degraded,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReferenceRuntimeStatus {
    Starting,
    Syncing,
    Ready,
    Degraded,
    Unavailable,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReferenceAppPhase {
    Booting,
    Loading,
    Serving,
    Ticking,
    Scanning,
    Reconciling,
    Publishing,
    Degraded,
    Stopping,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReferenceCatalogReadiness {
    Empty,
    Syncing,
    Ready,
    Degraded,
    Invalid,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReferenceSourcePhase {
    Disabled,
    Registered,
    Idle,
    Scanning,
    Promoting,
    Syncing,
    Ready,
    Degraded,
    Unavailable,
    Paused,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReferenceSourceProgressKind {
    Unknown,
    Complete,
    Paged,
    Scoped,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReferenceDiagnosticSeverity {
    Info,
    Warn,
    Error,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReferenceProviderStatus {
    Ready,
    Syncing,
    Degraded,
    Paused,
    Disabled,
}

#[cfg(test)]
mod tests {
    use kairos_primitives::integration::ProviderId;
    use kairos_primitives::reference::{AssetClass, AssetId, ReferenceStatus, Symbol};

    use super::{
        ReferenceAppPhase, ReferenceAppRuntimeError, ReferenceAppRuntimeStatus,
        ReferenceCatalogIntegrityStatus, ReferenceCatalogReadiness, ReferenceCatalogRuntimeStatus,
        ReferenceProviderProduct, ReferencePublicationRuntimeError,
        ReferencePublicationRuntimeStatus, ReferenceSourceControlRequest,
        ReferenceSourceDefinitionRequest, ReferenceSourceDesiredState, ReferenceSourceKind,
        ReferenceSourcePhase, ReferenceSourceProgress, ReferenceSourceProgressKind,
        ReferenceSourceRuntimeError, ReferenceSourceRuntimeStatus, ReferenceSourceScope,
        ReferenceSourceScopeKind, ReferenceSourceScopeRequest, ReferenceSourceSyncPolicy,
        ReferenceSourceTickBudget, ReferenceSourceWorkItem, ReferenceUpsertConflictPolicy,
        ReferenceUpsertProvenance, UpsertAssetRequest,
    };

    #[test]
    fn administrative_mutation_is_owned_by_the_reference_contract() {
        let request = UpsertAssetRequest {
            asset_id: AssetId::new("asset:btc").unwrap(),
            code: Symbol::new("BTC").unwrap(),
            name: Some("Bitcoin".into()),
            asset_class: AssetClass::Crypto,
            status: ReferenceStatus::Active,
            provenance: ReferenceUpsertProvenance::Manual,
            conflict_policy: ReferenceUpsertConflictPolicy::RejectProviderOwned,
        };
        let value = serde_json::to_value(request).unwrap();
        assert_eq!(value["asset_id"], "asset:btc");
        assert_eq!(value["asset_class"], "crypto");
        assert_eq!(value["status"], "active");
        assert_eq!(value["provenance"], "manual");
        assert_eq!(value["conflict_policy"], "reject_provider_owned");
    }

    #[test]
    fn source_desired_state_is_typed_but_keeps_json_shape() {
        let status = ReferenceSourceRuntimeStatus {
            source_id: ProviderId::new("massive-options").unwrap(),
            provider_id: Some(ProviderId::new("massive").unwrap()),
            provider_product: Some(ReferenceProviderProduct::Options),
            source_kind: ReferenceSourceKind::Scoped,
            configured: true,
            enabled: false,
            paused: false,
            desired_state: Some(ReferenceSourceDesiredState::Disabled),
            sync_policy: Some(ReferenceSourceSyncPolicy::ScopedSnapshot),
            scope: Some(ReferenceSourceScope {
                kind: ReferenceSourceScopeKind::UnderlyingInstrument,
                id: Some("instrument:equity:US:SPY:common".into()),
            }),
            credential_binding_present: Some(true),
            phase: ReferenceSourcePhase::Disabled,
            progress: ReferenceSourceProgress {
                kind: ReferenceSourceProgressKind::Unknown,
                pages_done: None,
                pages_total: None,
                records_seen: None,
                records_changed: None,
                scope_id: None,
                scope_kind: None,
                cursor_present: None,
            },
            work_item: ReferenceSourceWorkItem::default(),
            last_attempt_unix_nanos: None,
            last_success_unix_nanos: None,
            retry_after_unix_nanos: None,
            retry_backoff_seconds: None,
            consecutive_failures: 0,
            stale: false,
            has_last_known_good: false,
            last_error: Some(ReferenceSourceRuntimeError {
                code: "reference.provider_failed".into(),
                retryable: true,
                record_kind: None,
                record_id: None,
                message: "reference provider failed: HTTP 429".into(),
            }),
        };

        let value = serde_json::to_value(&status).unwrap();
        assert_eq!(value["provider_id"], "massive");
        assert_eq!(value["provider_product"], "options");
        assert_eq!(value["source_kind"], "scoped");
        assert_eq!(value["configured"], true);
        assert_eq!(value["enabled"], false);
        assert_eq!(value["paused"], false);
        assert_eq!(value["desired_state"], "disabled");
        assert_eq!(value["sync_policy"], "scoped_snapshot");
        assert_eq!(value["scope"]["kind"], "underlying_instrument");
        assert_eq!(value["scope"]["id"], "instrument:equity:US:SPY:common");
        assert_eq!(value["credential_binding_present"], true);
        assert_eq!(value["last_error"]["code"], "reference.provider_failed");
        assert_eq!(value["last_error"]["retryable"], true);
        let decoded: ReferenceSourceRuntimeStatus = serde_json::from_value(value).unwrap();
        assert_eq!(
            decoded.provider_id,
            Some(ProviderId::new("massive").unwrap())
        );
        assert_eq!(
            decoded.provider_product,
            Some(ReferenceProviderProduct::Options)
        );
        assert_eq!(
            decoded.last_error.as_ref().map(|error| error.code.as_str()),
            Some("reference.provider_failed")
        );
        assert_eq!(
            decoded.desired_state,
            Some(ReferenceSourceDesiredState::Disabled)
        );
        assert_eq!(
            decoded.sync_policy,
            Some(ReferenceSourceSyncPolicy::ScopedSnapshot)
        );
        assert_eq!(
            decoded.scope.as_ref().map(|scope| scope.kind),
            Some(ReferenceSourceScopeKind::UnderlyingInstrument)
        );
        assert_eq!(decoded.credential_binding_present, Some(true));
    }

    #[test]
    fn source_control_request_keeps_json_shape() {
        let request = ReferenceSourceControlRequest {
            source_id: ProviderId::new("massive-options").unwrap(),
            desired_state: ReferenceSourceDesiredState::Paused,
        };

        let value = serde_json::to_value(&request).unwrap();
        assert_eq!(value["source_id"], "massive-options");
        assert_eq!(value["desired_state"], "paused");

        let decoded: ReferenceSourceControlRequest = serde_json::from_value(value).unwrap();
        assert_eq!(
            decoded.source_id,
            ProviderId::new("massive-options").unwrap()
        );
        assert_eq!(decoded.desired_state, ReferenceSourceDesiredState::Paused);
    }

    #[test]
    fn app_runtime_status_keeps_tick_error_json_shape() {
        let status = ReferenceAppRuntimeStatus {
            phase: ReferenceAppPhase::Degraded,
            actor_id: "reference".into(),
            source_id: "reference-default".into(),
            refresh_interval_millis: 300_000,
            last_tick_started_unix_nanos: None,
            last_tick_finished_unix_nanos: None,
            last_tick_duration_millis: Some(125),
            next_tick_due_unix_nanos: None,
            active_work_item_count: 1,
            queued_work_item_count: 2,
            last_error: Some(ReferenceAppRuntimeError {
                code: "reference.provider_failed".into(),
                retryable: true,
                message: "reference provider failed: timeout".into(),
            }),
            tick_budget: ReferenceSourceTickBudget::default(),
        };

        let value = serde_json::to_value(&status).unwrap();
        assert_eq!(value["phase"], "degraded");
        assert_eq!(value["last_tick_duration_millis"], 125);
        assert_eq!(value["active_work_item_count"], 1);
        assert_eq!(value["queued_work_item_count"], 2);
        assert_eq!(value["last_error"]["code"], "reference.provider_failed");
        assert_eq!(value["last_error"]["retryable"], true);

        let decoded: ReferenceAppRuntimeStatus = serde_json::from_value(value).unwrap();
        assert_eq!(
            decoded.last_error.as_ref().map(|error| error.code.as_str()),
            Some("reference.provider_failed")
        );
    }

    #[test]
    fn publication_runtime_status_exposes_backlog_shape_with_defaults() {
        let status = ReferencePublicationRuntimeStatus {
            pending_publication_count: 5,
            backlog_degraded: true,
            batch_limit: 1024,
            oldest_pending_event_id: Some("reference:00000000000000000042".into()),
            last_error: Some(ReferencePublicationRuntimeError {
                code: "reference.publication_failed".into(),
                retryable: true,
                message: "reference publication failed: missing publisher".into(),
            }),
        };

        let value = serde_json::to_value(&status).unwrap();
        assert_eq!(value["pending_publication_count"], 5);
        assert_eq!(value["backlog_degraded"], true);
        assert_eq!(value["batch_limit"], 1024);
        assert_eq!(
            value["oldest_pending_event_id"],
            "reference:00000000000000000042"
        );
        assert_eq!(value["last_error"]["code"], "reference.publication_failed");

        let decoded: ReferencePublicationRuntimeStatus =
            serde_json::from_value(serde_json::json!({
                "pending_publication_count": 5
            }))
            .unwrap();
        assert_eq!(decoded.pending_publication_count, 5);
        assert!(!decoded.backlog_degraded);
        assert_eq!(decoded.batch_limit, 0);
        assert_eq!(decoded.oldest_pending_event_id, None);
        assert_eq!(decoded.last_error, None);
    }

    #[test]
    fn catalog_runtime_status_exposes_catalog_shape_with_defaults() {
        let status = ReferenceCatalogRuntimeStatus {
            readiness: ReferenceCatalogReadiness::Ready,
            generation: 7.into(),
            event_sequence: 11.into(),
            committed_at_unix_nanos: 123.into(),
            entity_count: 1,
            asset_count: 2,
            instrument_count: 3,
            listing_count: 4,
            market_count: 5,
            active_market_count: 6,
            lifecycle_event_count: 7,
            integrity: ReferenceCatalogIntegrityStatus {
                degraded: true,
                missing_equity_market_count: 8,
                legacy_exchange_market_id_count: 9,
                legacy_exchange_listing_id_count: 10,
                option_listing_count: 11,
                option_market_count: 12,
            },
        };

        let value = serde_json::to_value(&status).unwrap();
        assert_eq!(value["committed_at_unix_nanos"], 123);
        assert_eq!(value["entity_count"], 1);
        assert_eq!(value["asset_count"], 2);
        assert_eq!(value["instrument_count"], 3);
        assert_eq!(value["listing_count"], 4);
        assert_eq!(value["market_count"], 5);
        assert_eq!(value["active_market_count"], 6);
        assert_eq!(value["lifecycle_event_count"], 7);
        assert_eq!(value["integrity"]["degraded"], true);
        assert_eq!(value["integrity"]["missing_equity_market_count"], 8);
        assert_eq!(value["integrity"]["legacy_exchange_market_id_count"], 9);
        assert_eq!(value["integrity"]["legacy_exchange_listing_id_count"], 10);
        assert_eq!(value["integrity"]["option_listing_count"], 11);
        assert_eq!(value["integrity"]["option_market_count"], 12);

        let decoded: ReferenceCatalogRuntimeStatus = serde_json::from_value(serde_json::json!({
            "readiness": "ready",
            "generation": 7,
            "event_sequence": 11,
            "market_count": 5
        }))
        .unwrap();
        assert_eq!(decoded.generation, 7.into());
        assert_eq!(decoded.event_sequence, 11.into());
        assert_eq!(decoded.committed_at_unix_nanos, 0.into());
        assert_eq!(decoded.entity_count, 0);
        assert_eq!(decoded.asset_count, 0);
        assert_eq!(decoded.instrument_count, 0);
        assert_eq!(decoded.listing_count, 0);
        assert_eq!(decoded.market_count, 5);
        assert_eq!(decoded.active_market_count, 0);
        assert_eq!(decoded.lifecycle_event_count, 0);
        assert_eq!(
            decoded.integrity,
            ReferenceCatalogIntegrityStatus::default()
        );
    }

    #[test]
    fn source_scope_request_keeps_json_shape() {
        let request = ReferenceSourceScopeRequest {
            source_id: ProviderId::new("massive-options").unwrap(),
            scope: ReferenceSourceScope {
                kind: ReferenceSourceScopeKind::UnderlyingInstrument,
                id: Some("instrument:equity:US:SPY:common".into()),
            },
            enabled: true,
        };

        let value = serde_json::to_value(&request).unwrap();
        assert_eq!(value["source_id"], "massive-options");
        assert_eq!(value["scope"]["kind"], "underlying_instrument");
        assert_eq!(value["scope"]["id"], "instrument:equity:US:SPY:common");
        assert_eq!(value["enabled"], true);

        let decoded: ReferenceSourceScopeRequest = serde_json::from_value(value).unwrap();
        assert_eq!(
            decoded.source_id,
            ProviderId::new("massive-options").unwrap()
        );
        assert_eq!(
            decoded.scope.kind,
            ReferenceSourceScopeKind::UnderlyingInstrument
        );
        assert!(decoded.enabled);
    }

    #[test]
    fn registered_source_phase_keeps_json_shape() {
        let value = serde_json::to_value(ReferenceSourcePhase::Registered).unwrap();
        assert_eq!(value, "registered");
        let decoded: ReferenceSourcePhase = serde_json::from_value(value).unwrap();
        assert_eq!(decoded, ReferenceSourcePhase::Registered);
    }

    #[test]
    fn source_definition_request_keeps_json_shape() {
        let request = ReferenceSourceDefinitionRequest {
            source_id: ProviderId::new("massive-options").unwrap(),
            provider_id: ProviderId::new("massive").unwrap(),
            provider_product: Some(ReferenceProviderProduct::Options),
            scope: ReferenceSourceScope {
                kind: ReferenceSourceScopeKind::UnderlyingInstrument,
                id: Some("instrument:equity:US:SPY:common".into()),
            },
            desired_state: ReferenceSourceDesiredState::Enabled,
            credential_binding: Some("massive.default".into()),
            sync_policy: ReferenceSourceSyncPolicy::ScopedSnapshot,
        };

        let value = serde_json::to_value(&request).unwrap();
        assert_eq!(value["source_id"], "massive-options");
        assert_eq!(value["provider_id"], "massive");
        assert_eq!(value["provider_product"], "options");
        assert_eq!(value["scope"]["kind"], "underlying_instrument");
        assert_eq!(value["scope"]["id"], "instrument:equity:US:SPY:common");
        assert_eq!(value["desired_state"], "enabled");
        assert_eq!(value["credential_binding"], "massive.default");
        assert_eq!(value["sync_policy"], "scoped_snapshot");

        let decoded: ReferenceSourceDefinitionRequest = serde_json::from_value(value).unwrap();
        assert_eq!(
            decoded.provider_product,
            Some(ReferenceProviderProduct::Options)
        );
        assert_eq!(
            decoded.scope.kind,
            ReferenceSourceScopeKind::UnderlyingInstrument
        );
        assert_eq!(
            decoded.sync_policy,
            ReferenceSourceSyncPolicy::ScopedSnapshot
        );
    }
}
