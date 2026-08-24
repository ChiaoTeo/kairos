//! Reference domain exchanges and provider snapshots.

use std::collections::BTreeSet;

use kairos_primitives::decimal::{Money, Price, Quantity};
use kairos_primitives::market::Provider;
use kairos_primitives::reference::{
    AssetClass, AssetId, ExchangeId, InstrumentId, InstrumentKind, IssuerId, ListingId, MarketId,
    ReferenceSourceId, ReferenceStatus, Symbol,
};
use kairos_primitives::time::{Generation, UnixNanos};
use serde::{Deserialize, Serialize};

use super::{ReferenceError, ReferenceResult};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReferenceSourceDefinition {
    pub source_id: ReferenceSourceId,
    pub provider_id: Provider,
    #[serde(default)]
    pub scope: SourceScope,
    pub desired_state: SourceDesiredState,
    #[serde(default)]
    pub credential_binding: Option<SourceCredentialBinding>,
    pub sync_policy: SourceSyncPolicy,
}

impl ReferenceSourceDefinition {
    /// Default definition for a source supplied directly to the Reference
    /// runtime. Code-owned production adapters may replace these defaults at
    /// the services boundary without making the domain depend on that registry.
    pub(crate) fn runtime_default(source_id: &str) -> ReferenceResult<Self> {
        Ok(Self {
            source_id: ReferenceSourceId::new(source_id)?,
            provider_id: Provider::new(source_id)?,
            scope: SourceScope::global(),
            desired_state: SourceDesiredState::Enabled,
            credential_binding: None,
            sync_policy: SourceSyncPolicy::FullSnapshot,
        })
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(transparent)]
pub struct SourceCredentialBinding(String);

impl SourceCredentialBinding {
    pub fn new(value: impl Into<String>) -> ReferenceResult<Self> {
        let value = value.into();
        if value.trim().is_empty() {
            return Err(ReferenceError::Invalid(
                "source credential binding cannot be empty".into(),
            ));
        }
        if value.trim() != value {
            return Err(ReferenceError::Invalid(
                "source credential binding cannot contain leading or trailing whitespace".into(),
            ));
        }
        Ok(Self(value))
    }

    pub fn as_str(&self) -> &str {
        self.0.as_str()
    }
}

impl<'de> Deserialize<'de> for SourceCredentialBinding {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        let value = String::deserialize(deserializer)?;
        Self::new(value).map_err(serde::de::Error::custom)
    }
}

impl std::ops::Deref for SourceCredentialBinding {
    type Target = str;

    fn deref(&self) -> &Self::Target {
        self.as_str()
    }
}

impl std::fmt::Display for SourceCredentialBinding {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(self.as_str())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SourceScope {
    pub kind: SourceScopeKind,
    #[serde(default)]
    pub id: Option<String>,
}

impl Default for SourceScope {
    fn default() -> Self {
        Self::global()
    }
}

impl SourceScope {
    pub fn global() -> Self {
        Self {
            kind: SourceScopeKind::Global,
            id: None,
        }
    }

    pub fn provider_catalog() -> Self {
        Self {
            kind: SourceScopeKind::ProviderCatalog,
            id: None,
        }
    }

    pub fn underlying_instrument(id: impl Into<String>) -> Self {
        Self {
            kind: SourceScopeKind::UnderlyingInstrument,
            id: Some(id.into()),
        }
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SourceScopeKind {
    #[default]
    Global,
    ProviderCatalog,
    UnderlyingInstrument,
    Coverage,
    Custom,
}

impl SourceScopeKind {
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

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SourceDesiredState {
    #[default]
    Enabled,
    Disabled,
    Paused,
    Removed,
}

impl SourceDesiredState {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Enabled => "enabled",
            Self::Disabled => "disabled",
            Self::Paused => "paused",
            Self::Removed => "removed",
        }
    }
}

impl From<&str> for SourceDesiredState {
    fn from(value: &str) -> Self {
        match value.trim().to_ascii_lowercase().as_str() {
            "enabled" => Self::Enabled,
            "disabled" => Self::Disabled,
            "paused" => Self::Paused,
            "removed" => Self::Removed,
            _ => Self::Enabled,
        }
    }
}

impl From<String> for SourceDesiredState {
    fn from(value: String) -> Self {
        Self::from(value.as_str())
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SourceSyncPolicy {
    #[default]
    FullSnapshot,
    PagedSnapshot,
    ScopedSnapshot,
    IncrementalDelta,
    ManualCurated,
}

impl SourceSyncPolicy {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::FullSnapshot => "full_snapshot",
            Self::PagedSnapshot => "paged_snapshot",
            Self::ScopedSnapshot => "scoped_snapshot",
            Self::IncrementalDelta => "incremental_delta",
            Self::ManualCurated => "manual_curated",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SourceWorkItem {
    pub work_item_id: String,
    pub source_id: String,
    pub scope: SourceScope,
    pub reason: SourceWorkReason,
    pub budget: SourceTickBudget,
}

impl SourceWorkItem {
    pub fn runtime_snapshot(&self, cursor_present: Option<bool>) -> SourceRuntimeWorkItem {
        SourceRuntimeWorkItem {
            work_item_id: Some(self.work_item_id.clone()),
            scope_id: self.scope.id.clone(),
            scope_kind: Some(self.scope.kind.as_str().to_owned()),
            cursor_present,
            skip_reason: None,
        }
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct AffectedReferenceSet {
    pub exchanges: BTreeSet<String>,
    pub assets: BTreeSet<String>,
    pub instruments: BTreeSet<String>,
    pub listings: BTreeSet<String>,
    pub markets: BTreeSet<String>,
    pub requires_full_replace: bool,
}

impl AffectedReferenceSet {
    pub fn from_events<'a>(events: impl IntoIterator<Item = &'a LifecycleEvent>) -> Self {
        let mut affected = Self::default();
        for event in events {
            let Some(kind) = event.record_kind.as_deref() else {
                affected.requires_full_replace = true;
                continue;
            };
            let Some(id) = event.record_id.as_deref() else {
                affected.requires_full_replace = true;
                continue;
            };
            match kind {
                "exchange" => {
                    affected.exchanges.insert(id.to_owned());
                },
                "asset" => {
                    affected.assets.insert(id.to_owned());
                },
                "instrument" => {
                    affected.instruments.insert(id.to_owned());
                },
                "listing" => {
                    affected.listings.insert(id.to_owned());
                },
                "market" => {
                    affected.markets.insert(id.to_owned());
                },
                _ => affected.requires_full_replace = true,
            }
        }
        affected
    }

    pub fn is_empty(&self) -> bool {
        self.exchanges.is_empty()
            && self.assets.is_empty()
            && self.instruments.is_empty()
            && self.listings.is_empty()
            && self.markets.is_empty()
            && !self.requires_full_replace
    }

    pub fn total_count(&self) -> usize {
        self.exchanges.len()
            + self.assets.len()
            + self.instruments.len()
            + self.listings.len()
            + self.markets.len()
    }

    pub const fn write_mode(&self) -> &'static str {
        if self.requires_full_replace {
            "full_replace"
        } else {
            "affected_update"
        }
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SourceWorkReason {
    #[default]
    ScheduledTick,
    RpcRefresh,
    CoverageChanged,
    Startup,
    Retry,
}

impl SourceWorkReason {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::ScheduledTick => "scheduled_tick",
            Self::RpcRefresh => "rpc_refresh",
            Self::CoverageChanged => "coverage_changed",
            Self::Startup => "startup",
            Self::Retry => "retry",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SourceTickBudget {
    pub max_sources_per_tick: u32,
    pub max_batches_per_source: u32,
    pub max_records_per_batch: Option<u64>,
    pub max_wall_clock_millis: Option<u64>,
    pub max_publications_per_tick: Option<u32>,
}

impl Default for SourceTickBudget {
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

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SourceHealth {
    pub source_id: String,
    #[serde(default)]
    pub definition: Option<ReferenceSourceDefinition>,
    pub status: SourceRuntimePhase,
    #[serde(default)]
    pub progress: SourceRuntimeProgress,
    #[serde(default)]
    pub work_item: SourceRuntimeWorkItem,
    pub last_attempt_unix_nanos: Option<UnixNanos>,
    pub last_success_unix_nanos: Option<UnixNanos>,
    #[serde(default)]
    pub retry_after_unix_nanos: Option<UnixNanos>,
    #[serde(default)]
    pub retry_backoff_seconds: Option<u64>,
    pub consecutive_failures: u32,
    pub stale: bool,
    #[serde(default)]
    pub last_error: Option<SourceRuntimeError>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SourceRuntimeError {
    pub code: String,
    pub retryable: bool,
    #[serde(default)]
    pub record_kind: Option<String>,
    #[serde(default)]
    pub record_id: Option<String>,
    pub message: String,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SourceRuntimePhase {
    #[default]
    #[serde(alias = "unknown")]
    Idle,
    Registered,
    Ready,
    Scanning,
    Promoting,
    Syncing,
    Degraded,
    Unavailable,
    Paused,
    Disabled,
}

impl SourceRuntimePhase {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Idle => "idle",
            Self::Registered => "registered",
            Self::Ready => "ready",
            Self::Scanning => "scanning",
            Self::Promoting => "promoting",
            Self::Syncing => "syncing",
            Self::Degraded => "degraded",
            Self::Unavailable => "unavailable",
            Self::Paused => "paused",
            Self::Disabled => "disabled",
        }
    }

    pub const fn is_healthy_for_catalog(self) -> bool {
        matches!(
            self,
            Self::Idle | Self::Ready | Self::Scanning | Self::Promoting
        )
    }
}

impl From<SourceRuntimePhase> for String {
    fn from(value: SourceRuntimePhase) -> Self {
        value.as_str().to_owned()
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct SourceRuntimeProgress {
    pub kind: SourceRuntimeProgressKind,
    pub pages_done: Option<u64>,
    pub pages_total: Option<u64>,
    pub records_seen: Option<u64>,
    pub records_changed: Option<u64>,
}

impl SourceRuntimeProgress {
    pub const fn unknown() -> Self {
        Self {
            kind: SourceRuntimeProgressKind::Unknown,
            pages_done: None,
            pages_total: None,
            records_seen: None,
            records_changed: None,
        }
    }

    pub const fn complete(
        pages_done: Option<u64>,
        pages_total: Option<u64>,
        records_seen: Option<u64>,
        records_changed: Option<u64>,
    ) -> Self {
        Self {
            kind: SourceRuntimeProgressKind::Complete,
            pages_done,
            pages_total,
            records_seen,
            records_changed,
        }
    }

    pub const fn paged(
        pages_done: Option<u64>,
        pages_total: Option<u64>,
        records_seen: Option<u64>,
        records_changed: Option<u64>,
    ) -> Self {
        Self {
            kind: SourceRuntimeProgressKind::Paged,
            pages_done,
            pages_total,
            records_seen,
            records_changed,
        }
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SourceRuntimeProgressKind {
    #[default]
    Unknown,
    Complete,
    Paged,
    Scoped,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct SourceRuntimeWorkItem {
    pub work_item_id: Option<String>,
    pub scope_id: Option<String>,
    pub scope_kind: Option<String>,
    pub cursor_present: Option<bool>,
    #[serde(default)]
    pub skip_reason: Option<String>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Exchange {
    #[serde(default)]
    pub source_id: Option<String>,
    pub exchange_id: ExchangeId,
    pub name: String,
    pub status: ReferenceStatus,
}

impl Exchange {
    pub fn normalize_canonical_name(&mut self) {
        if let Some(name) = canonical_exchange_name(self.exchange_id.as_str()) {
            self.name = name.into();
        }
    }
}

pub fn canonical_exchange_name(exchange_id: &str) -> Option<&'static str> {
    match exchange_id {
        "exchange:nasdaq" => Some("Nasdaq"),
        "exchange:nyse" => Some("NYSE"),
        "exchange:amex" => Some("NYSE American"),
        "exchange:arcx" => Some("NYSE Arca"),
        "exchange:bats" => Some("Cboe BZX Exchange"),
        "exchange:cboe-bzx-options" => Some("Cboe BZX Options Exchange"),
        _ => None,
    }
}

#[cfg(test)]
mod source_workflow_tests {
    use kairos_primitives::market::Provider;
    use kairos_primitives::reference::ReferenceSourceId;

    use super::{
        ReferenceSourceDefinition, SourceCredentialBinding, SourceDesiredState, SourceScope,
        SourceSyncPolicy, SourceTickBudget, SourceWorkItem, SourceWorkReason,
    };
    #[test]
    fn source_definition_credential_binding_is_typed_but_serializes_as_string() {
        let definition = ReferenceSourceDefinition {
            source_id: ReferenceSourceId::new("massive-options").unwrap(),
            provider_id: Provider::new("massive").unwrap(),
            scope: SourceScope::global(),
            desired_state: SourceDesiredState::Enabled,
            credential_binding: Some(SourceCredentialBinding::new("massive.default").unwrap()),
            sync_policy: SourceSyncPolicy::ScopedSnapshot,
        };

        let value = serde_json::to_value(&definition).unwrap();
        assert_eq!(value["credential_binding"], "massive.default");

        let invalid = serde_json::json!({
            "source_id": "massive-options",
            "provider_id": "massive",
            "desired_state": "enabled",
            "credential_binding": " massive.default ",
            "sync_policy": "scoped_snapshot"
        });
        assert!(serde_json::from_value::<ReferenceSourceDefinition>(invalid).is_err());
    }

    #[test]
    fn persisted_source_definition_ignores_legacy_provider_product() {
        let legacy = serde_json::json!({
            "source_id": "massive-options",
            "provider_id": "massive",
            "provider_product": "options",
            "scope": { "kind": "global", "id": null },
            "desired_state": "enabled",
            "credential_binding": null,
            "sync_policy": "scoped_snapshot"
        });

        let definition: ReferenceSourceDefinition = serde_json::from_value(legacy).unwrap();
        assert_eq!(definition.source_id.as_str(), "massive-options");
        let current = serde_json::to_value(definition).unwrap();
        assert!(current.get("provider_product").is_none());
    }

    #[test]
    fn source_work_item_projects_to_runtime_snapshot() {
        let work_item = SourceWorkItem {
            work_item_id: "massive-options:AAPL".to_owned(),
            source_id: "massive-options".to_owned(),
            scope: SourceScope::underlying_instrument("instrument:equity:US:AAPL:common"),
            reason: SourceWorkReason::RpcRefresh,
            budget: SourceTickBudget::default(),
        };

        let snapshot = work_item.runtime_snapshot(Some(true));

        assert_eq!(
            snapshot.work_item_id.as_deref(),
            Some("massive-options:AAPL")
        );
        assert_eq!(
            snapshot.scope_id.as_deref(),
            Some("instrument:equity:US:AAPL:common")
        );
        assert_eq!(
            snapshot.scope_kind.as_deref(),
            Some("underlying_instrument")
        );
        assert_eq!(snapshot.cursor_present, Some(true));
    }

    #[test]
    fn affected_reference_set_groups_known_event_records() {
        let events = vec![
            super::LifecycleEvent {
                record_kind: Some("asset".into()),
                record_id: Some("asset:BTC".into()),
                ..super::LifecycleEvent::default()
            },
            super::LifecycleEvent {
                record_kind: Some("market".into()),
                record_id: Some("market:binance:spot:btc-usdt".into()),
                ..super::LifecycleEvent::default()
            },
        ];

        let affected = super::AffectedReferenceSet::from_events(&events);

        assert!(!affected.requires_full_replace);
        assert!(affected.assets.contains("asset:BTC"));
        assert!(affected.markets.contains("market:binance:spot:btc-usdt"));
        assert!(affected.exchanges.is_empty());
        assert!(affected.instruments.is_empty());
        assert!(affected.listings.is_empty());
        assert_eq!(affected.total_count(), 2);
        assert_eq!(affected.write_mode(), "affected_update");
    }

    #[test]
    fn affected_reference_set_falls_back_for_unidentified_event() {
        let events = vec![super::LifecycleEvent {
            record_kind: Some("unknown".into()),
            record_id: Some("id".into()),
            ..super::LifecycleEvent::default()
        }];

        let affected = super::AffectedReferenceSet::from_events(&events);

        assert!(affected.requires_full_replace);
        assert_eq!(affected.write_mode(), "full_replace");
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Asset {
    #[serde(default)]
    pub source_id: Option<String>,
    pub asset_id: AssetId,
    pub code: Symbol,
    pub name: Option<String>,
    pub asset_class: AssetClass,
    pub status: ReferenceStatus,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Instrument {
    #[serde(default)]
    pub source_id: Option<String>,
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

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Listing {
    #[serde(default)]
    pub source_id: Option<String>,
    pub listing_id: ListingId,
    pub instrument_id: InstrumentId,
    pub exchange_id: ExchangeId,
    pub exchange_symbol: Symbol,
    pub status: ReferenceStatus,
    pub effective_from_unix_nanos: UnixNanos,
    pub effective_to_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Market {
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    #[serde(default)]
    pub listing_id: Option<ListingId>,
    pub exchange_id: ExchangeId,
    pub instrument_kind: InstrumentKind,
    #[serde(default)]
    pub asset_type: Option<AssetClass>,
    #[serde(default)]
    pub underlying_instrument_id: Option<InstrumentId>,
    #[serde(default)]
    pub venue_symbol: Option<Symbol>,
    pub base_asset_id: Option<AssetId>,
    pub quote_asset_id: Option<AssetId>,
    pub status: ReferenceStatus,
    pub price_tick: Option<Price>,
    pub quantity_tick: Option<Quantity>,
    pub price_precision: i32,
    pub quantity_precision: i32,
    pub minimum_quantity: Option<Quantity>,
    pub minimum_notional: Option<Money>,
    pub contract_size: Option<Quantity>,
    pub effective_from_unix_nanos: UnixNanos,
    pub effective_to_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct LifecycleEvent {
    pub event_id: String,
    pub event_type: String,
    pub event_time_unix_nanos: UnixNanos,
    #[serde(default)]
    pub record_kind: Option<String>,
    #[serde(default)]
    pub record_id: Option<String>,
    pub market_id: Option<MarketId>,
    pub instrument_id: Option<InstrumentId>,
    pub listing_id: Option<ListingId>,
    pub exchange_id: Option<ExchangeId>,
    pub venue_symbol: Option<Symbol>,
    pub previous_status: Option<ReferenceStatus>,
    pub current_status: Option<ReferenceStatus>,
    pub previous_symbol: Option<String>,
    pub current_symbol: Option<String>,
    #[serde(default)]
    pub operation: Option<String>,
    #[serde(default)]
    pub provenance: Option<String>,
    #[serde(default)]
    pub conflict_policy: Option<String>,
    #[serde(default)]
    pub generation: Generation,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ProviderCatalog {
    pub exchanges: Vec<Exchange>,
    pub assets: Vec<Asset>,
    pub instruments: Vec<Instrument>,
    pub listings: Vec<Listing>,
    pub markets: Vec<Market>,
}

impl ProviderCatalog {
    pub fn record_count(&self) -> usize {
        self.exchanges.len()
            + self.assets.len()
            + self.instruments.len()
            + self.listings.len()
            + self.markets.len()
    }

    /// Merge independently authoritative provider catalogs into one
    /// canonical candidate. This is a Reference domain rule: persistence and
    /// provider composition must not each invent their own conflict policy.
    pub fn merge<'a>(
        catalogs: impl IntoIterator<Item = &'a ProviderCatalog>,
    ) -> ReferenceResult<Self> {
        let mut exchanges = std::collections::BTreeMap::new();
        let mut assets = std::collections::BTreeMap::new();
        let mut instruments = std::collections::BTreeMap::new();
        let mut listings = std::collections::BTreeMap::new();
        let mut markets = std::collections::BTreeMap::new();
        let mut conflicts = Vec::new();

        macro_rules! merge_exact {
            ($catalog:expr, $field:ident, $key:expr, $label:literal) => {
                for value in &$catalog.$field {
                    let key = $key(value);
                    if $field
                        .insert(key.clone(), value.clone())
                        .is_some_and(|previous| previous != *value)
                    {
                        conflicts.push(format!(
                            concat!("irreconcilable canonical ", $label, " conflict for {}"),
                            key
                        ));
                    }
                }
            };
        }

        for catalog in catalogs {
            merge_exact!(
                catalog,
                exchanges,
                |value: &Exchange| value.exchange_id.clone(),
                "exchange"
            );
            for value in &catalog.assets {
                if let Some(previous) = assets.get_mut(&value.asset_id) {
                    merge_asset(previous, value).map_err(|reason| {
                        ReferenceError::Invalid(format!(
                            "canonical asset conflict for {}: {reason}",
                            value.asset_id
                        ))
                    })?;
                } else {
                    assets.insert(value.asset_id.clone(), value.clone());
                }
            }
            for value in &catalog.instruments {
                if let Some(previous) = instruments.get_mut(&value.instrument_id) {
                    if previous != value {
                        merge_instrument(previous, value).map_err(|reason| {
                            ReferenceError::Invalid(format!(
                                "canonical instrument conflict for {}: {reason}",
                                value.instrument_id
                            ))
                        })?;
                    }
                } else {
                    instruments.insert(value.instrument_id.clone(), value.clone());
                }
            }
            merge_exact!(
                catalog,
                listings,
                |value: &Listing| value.listing_id.clone(),
                "listing"
            );
            merge_exact!(
                catalog,
                markets,
                |value: &Market| value.market_id.clone(),
                "market"
            );
        }

        if !conflicts.is_empty() {
            let sample = conflicts.iter().take(8).cloned().collect::<Vec<_>>();
            return Err(ReferenceError::Invalid(format!(
                "providers returned {} canonical record conflicts (sample: {})",
                conflicts.len(),
                sample.join(", ")
            )));
        }
        let candidate = Self {
            exchanges: exchanges.into_values().collect(),
            assets: assets.into_values().collect(),
            instruments: instruments.into_values().collect(),
            listings: listings.into_values().collect(),
            markets: markets.into_values().collect(),
        };
        Ok(candidate)
    }

    /// Validate the provider boundary before any actor-owned state is changed.
    pub fn validate(&self) -> ReferenceResult<()> {
        fn required(value: &str, label: &str) -> ReferenceResult<()> {
            if value.trim().is_empty() {
                return Err(ReferenceError::Invalid(format!("{label} is empty")));
            }
            Ok(())
        }

        fn interval(from: UnixNanos, to: Option<UnixNanos>, label: &str) -> ReferenceResult<()> {
            if to.is_some_and(|end| end <= from) {
                return Err(ReferenceError::Invalid(format!(
                    "{label} has an invalid effective interval"
                )));
            }
            Ok(())
        }

        fn unique<T, F>(values: &[T], label: &str, key: F) -> ReferenceResult<()>
        where
            F: Fn(&T) -> &str,
        {
            let mut ids = std::collections::BTreeSet::new();
            for value in values {
                let id = key(value);
                if id.is_empty() {
                    return Err(ReferenceError::Invalid(format!("{label} id is empty")));
                }
                if !ids.insert(id) {
                    return Err(ReferenceError::DuplicateId {
                        record_kind: label.to_owned(),
                        record_id: id.to_owned(),
                    });
                }
            }
            Ok(())
        }

        unique(&self.exchanges, "exchange", |value| &value.exchange_id)?;
        unique(&self.assets, "asset", |value| &value.asset_id)?;
        unique(&self.instruments, "instrument", |value| {
            &value.instrument_id
        })?;
        unique(&self.listings, "listing", |value| &value.listing_id)?;
        unique(&self.markets, "market", |value| &value.market_id)?;

        for exchange in &self.exchanges {
            required(
                &exchange.name,
                &format!("exchange {} name", exchange.exchange_id),
            )?;
            required(
                exchange.status.as_str(),
                &format!("exchange {} status", exchange.exchange_id),
            )?;
        }
        for asset in &self.assets {
            required(&asset.code, &format!("asset {} code", asset.asset_id))?;
            if asset.asset_class == AssetClass::Unknown {
                return Err(ReferenceError::Invalid(format!(
                    "asset {} has unknown canonical class",
                    asset.asset_id
                )));
            }
            required(
                asset.status.as_str(),
                &format!("asset {} status", asset.asset_id),
            )?;
        }
        for instrument in &self.instruments {
            required(
                &instrument.symbol,
                &format!("instrument {} symbol", instrument.instrument_id),
            )?;
            if instrument.instrument_type == InstrumentKind::Unknown {
                return Err(ReferenceError::Invalid(format!(
                    "instrument {} has unknown canonical kind",
                    instrument.instrument_id
                )));
            }
            required(
                instrument.status.as_str(),
                &format!("instrument {} status", instrument.instrument_id),
            )?;
            if instrument.instrument_type == InstrumentKind::Option
                && (instrument.expiry_unix_nanos.is_none()
                    || instrument.strike.is_none()
                    || !matches!(
                        instrument
                            .option_right
                            .as_deref()
                            .map(|value| value.to_ascii_lowercase())
                            .as_deref(),
                        Some("call" | "put" | "c" | "p")
                    ))
            {
                return Err(ReferenceError::Invalid(format!(
                    "option instrument {} requires expiry, strike and call/put right",
                    instrument.instrument_id
                )));
            }
            if instrument.instrument_type == InstrumentKind::Future
                && instrument.expiry_unix_nanos.is_none()
            {
                return Err(ReferenceError::Invalid(format!(
                    "future instrument {} requires expiry",
                    instrument.instrument_id
                )));
            }
            if instrument.instrument_type == InstrumentKind::Perpetual
                && instrument.expiry_unix_nanos.is_some()
            {
                return Err(ReferenceError::Invalid(format!(
                    "perpetual instrument {} must not have expiry",
                    instrument.instrument_id
                )));
            }
            if instrument.instrument_type != InstrumentKind::Option
                && (instrument.strike.is_some() || instrument.option_right.is_some())
            {
                return Err(ReferenceError::Invalid(format!(
                    "non-option instrument {} must not have option terms",
                    instrument.instrument_id
                )));
            }
            if let Some(underlying_id) = instrument.underlying_instrument_id.as_deref() {
                if underlying_id == instrument.instrument_id.as_str() {
                    return Err(ReferenceError::Invalid(format!(
                        "instrument {} cannot underlie itself",
                        instrument.instrument_id
                    )));
                }
                if !self
                    .instruments
                    .iter()
                    .any(|value| value.instrument_id.as_str() == underlying_id)
                {
                    return Err(ReferenceError::Invalid(format!(
                        "instrument {} references missing underlying instrument {}",
                        instrument.instrument_id, underlying_id
                    )));
                }
            }
        }

        let instrument_ids: std::collections::BTreeSet<_> = self
            .instruments
            .iter()
            .map(|value| value.instrument_id.as_str())
            .collect();
        let listing_ids: std::collections::BTreeSet<_> = self
            .listings
            .iter()
            .map(|value| value.listing_id.as_str())
            .collect();
        let exchange_ids: std::collections::BTreeSet<_> = self
            .exchanges
            .iter()
            .map(|value| value.exchange_id.as_str())
            .collect();
        let asset_ids: std::collections::BTreeSet<_> = self
            .assets
            .iter()
            .map(|value| value.asset_id.as_str())
            .collect();
        for listing in &self.listings {
            required(
                listing.exchange_symbol.as_str(),
                &format!("listing {} exchange symbol", listing.listing_id),
            )?;
            required(
                listing.status.as_str(),
                &format!("listing {} status", listing.listing_id),
            )?;
            interval(
                listing.effective_from_unix_nanos,
                listing.effective_to_unix_nanos,
                &format!("listing {}", listing.listing_id),
            )?;
            if !instrument_ids.contains(listing.instrument_id.as_str()) {
                return Err(ReferenceError::Invalid(format!(
                    "listing {} references missing instrument {}",
                    listing.listing_id, listing.instrument_id
                )));
            }
            if !exchange_ids.contains(listing.exchange_id.as_str()) {
                return Err(ReferenceError::Invalid(format!(
                    "listing {} references missing exchange {}",
                    listing.listing_id, listing.exchange_id
                )));
            }
        }
        for market in &self.markets {
            if market.asset_type == Some(AssetClass::Unknown) {
                return Err(ReferenceError::Invalid(format!(
                    "market {} has unknown asset class",
                    market.market_id
                )));
            }
            if market.instrument_kind == InstrumentKind::Unknown {
                return Err(ReferenceError::Invalid(format!(
                    "market {} instrument kind is unknown",
                    market.market_id
                )));
            }
            required(
                market.status.as_str(),
                &format!("market {} status", market.market_id),
            )?;
            if market.price_precision < 0 || market.quantity_precision < 0 {
                return Err(ReferenceError::Invalid(format!(
                    "market {} precision must not be negative",
                    market.market_id
                )));
            }
            if market
                .minimum_notional
                .is_some_and(|value| value.mantissa() < 0)
            {
                return Err(ReferenceError::Invalid(format!(
                    "market {} minimum notional must not be negative",
                    market.market_id
                )));
            }
            interval(
                market.effective_from_unix_nanos,
                market.effective_to_unix_nanos,
                &format!("market {}", market.market_id),
            )?;
            if !instrument_ids.contains(market.instrument_id.as_str()) {
                return Err(ReferenceError::Invalid(format!(
                    "market {} references missing instrument {}",
                    market.market_id, market.instrument_id
                )));
            }
            let instrument = self
                .instruments
                .iter()
                .find(|instrument| instrument.instrument_id == market.instrument_id)
                .expect("instrument membership checked above");
            if instrument.instrument_type != market.instrument_kind {
                return Err(ReferenceError::Invalid(format!(
                    "market {} kind {:?} does not match instrument {} kind {:?}",
                    market.market_id,
                    market.instrument_kind,
                    instrument.instrument_id,
                    instrument.instrument_type
                )));
            }
            if let Some(listing_id) = market.listing_id.as_ref() {
                if !listing_ids.contains(listing_id.as_str()) {
                    return Err(ReferenceError::Invalid(format!(
                        "market {} references missing listing {}",
                        market.market_id, listing_id
                    )));
                }
                let listing = self
                    .listings
                    .iter()
                    .find(|value| &value.listing_id == listing_id)
                    .expect("validated listing id must resolve");
                if listing.instrument_id != market.instrument_id {
                    return Err(ReferenceError::Invalid(format!(
                        "market {} instrument {} disagrees with listing {} instrument {}",
                        market.market_id, market.instrument_id, listing_id, listing.instrument_id
                    )));
                }
            }
            if !exchange_ids.contains(market.exchange_id.as_str()) {
                return Err(ReferenceError::Invalid(format!(
                    "market {} references missing exchange {}",
                    market.market_id, market.exchange_id
                )));
            }
            if let Some(underlying_id) = market.underlying_instrument_id.as_deref() {
                if !instrument_ids.contains(underlying_id) {
                    return Err(ReferenceError::Invalid(format!(
                        "market {} references missing underlying instrument {}",
                        market.market_id, underlying_id
                    )));
                }
            }
            if let Some(asset_id) = market.base_asset_id.as_deref() {
                if !asset_ids.contains(asset_id) {
                    return Err(ReferenceError::Invalid(format!(
                        "market {} references missing base asset {}",
                        market.market_id, asset_id
                    )));
                }
            }
            if let Some(asset_id) = market.quote_asset_id.as_deref() {
                if !asset_ids.contains(asset_id) {
                    return Err(ReferenceError::Invalid(format!(
                        "market {} references missing quote asset {}",
                        market.market_id, asset_id
                    )));
                }
            }
        }
        Ok(())
    }
}

pub(crate) fn reconcile_instruments(values: &mut Vec<Instrument>) -> ReferenceResult<()> {
    let mut reconciled = std::collections::BTreeMap::new();
    for value in std::mem::take(values) {
        if let Some(previous) = reconciled.get_mut(&value.instrument_id) {
            merge_instrument(previous, &value).map_err(|reason| {
                ReferenceError::Invalid(format!(
                    "provider produced conflicting canonical instrument {}: {reason}",
                    value.instrument_id
                ))
            })?;
        } else {
            reconciled.insert(value.instrument_id.clone(), value);
        }
    }
    *values = reconciled.into_values().collect();
    Ok(())
}

pub(crate) fn merge_instrument(
    previous: &mut Instrument,
    incoming: &Instrument,
) -> Result<(), String> {
    let status = merged_reference_status(previous.status, incoming.status);
    let mut left = previous.clone();
    let mut right = incoming.clone();
    left.source_id = None;
    right.source_id = None;
    left.status = status;
    right.status = status;
    let mut fields = Vec::new();
    macro_rules! merge_optional {
        ($field:ident) => {
            match (&left.$field, &right.$field) {
                (None, Some(value)) => left.$field = Some(value.clone()),
                (Some(value), None) => right.$field = Some(value.clone()),
                (Some(left_value), Some(right_value)) if left_value != right_value => {
                    fields.push(stringify!($field));
                },
                _ => {},
            }
        };
    }
    merge_optional!(name);
    merge_optional!(issuer_id);
    merge_optional!(share_class);
    merge_optional!(primary_currency_asset_id);
    merge_optional!(underlying_instrument_id);
    merge_optional!(expiry_unix_nanos);
    merge_optional!(strike);
    merge_optional!(option_right);
    if left != right {
        if left.symbol != right.symbol {
            fields.push("symbol");
        }
        if left.instrument_type != right.instrument_type {
            fields.push("instrument_type");
        }
        if fields.is_empty() {
            fields.push("canonical attributes");
        }
        return Err(format!("different {}", fields.join(", ")));
    }
    *previous = left;
    Ok(())
}

pub(crate) fn merge_asset(previous: &mut Asset, incoming: &Asset) -> Result<(), String> {
    let status = merged_reference_status(previous.status, incoming.status);
    let mut left = previous.clone();
    let mut right = incoming.clone();
    left.source_id = None;
    right.source_id = None;
    left.status = status;
    right.status = status;
    match (&left.name, &right.name) {
        (None, Some(value)) => left.name = Some(value.clone()),
        (Some(value), None) => right.name = Some(value.clone()),
        _ => {},
    }
    if left != right {
        let mut fields = Vec::new();
        if left.code != right.code {
            fields.push("code");
        }
        if left.asset_class != right.asset_class {
            fields.push("asset_class");
        }
        if left.name != right.name {
            fields.push("name");
        }
        if fields.is_empty() {
            fields.push("canonical attributes");
        }
        return Err(format!("different {}", fields.join(", ")));
    }
    *previous = left;
    Ok(())
}

fn merged_reference_status(
    left: kairos_primitives::reference::ReferenceStatus,
    right: kairos_primitives::reference::ReferenceStatus,
) -> kairos_primitives::reference::ReferenceStatus {
    use kairos_primitives::reference::ReferenceStatus;
    if matches!(left, ReferenceStatus::Active | ReferenceStatus::Trading)
        || matches!(right, ReferenceStatus::Active | ReferenceStatus::Trading)
    {
        ReferenceStatus::Active
    } else if left == right {
        left
    } else if left == ReferenceStatus::Unknown {
        right
    } else if right == ReferenceStatus::Unknown {
        left
    } else {
        ReferenceStatus::Inactive
    }
}
