//! Public Reference models used by SQLite payloads and change events.

use kairos_primitives::decimal::{Money, Price, Quantity};
use kairos_primitives::integration::ProviderId;
use kairos_primitives::reference::{
    AssetClass, AssetId, Exchange, InstrumentId, InstrumentKind, IssuerId, ListingId, MarketId,
    ReferenceStatus, Symbol,
};
use kairos_primitives::runtime::{ActorId, InstanceId, LaunchId, WorkspaceId};
use kairos_primitives::time::{Generation, Sequence, UnixNanos};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Entity {
    pub entity_id: String,
    pub entity_type: String,
    pub name: String,
    pub status: ReferenceStatus,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Asset {
    pub asset_id: AssetId,
    pub code: Symbol,
    pub name: Option<String>,
    pub asset_class: AssetClass,
    pub status: ReferenceStatus,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Instrument {
    pub instrument_id: InstrumentId,
    pub symbol: Symbol,
    pub name: Option<String>,
    pub instrument_type: InstrumentKind,
    /// Legacy wire/persistence slot retained while v2 readers migrate. New
    /// Reference records never populate a second canonical classification.
    #[serde(default)]
    pub product_family: Option<String>,
    pub issuer_id: Option<IssuerId>,
    pub share_class: Option<String>,
    pub primary_currency_asset_id: Option<AssetId>,
    pub underlying_instrument_id: Option<InstrumentId>,
    pub expiry_unix_nanos: Option<UnixNanos>,
    pub strike: Option<Price>,
    pub option_right: Option<String>,
    pub status: ReferenceStatus,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Listing {
    pub listing_id: ListingId,
    pub instrument_id: InstrumentId,
    pub exchange_id: Exchange,
    pub exchange_symbol: Symbol,
    pub status: ReferenceStatus,
    pub effective_from_unix_nanos: UnixNanos,
    pub effective_to_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Market {
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    pub listing_id: Option<ListingId>,
    pub exchange_id: Exchange,
    pub instrument_kind: InstrumentKind,
    pub asset_type: Option<AssetClass>,
    pub underlying_instrument_id: Option<InstrumentId>,
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
pub struct ProviderHealthState {
    pub provider_id: ProviderId,
    pub status: String,
    pub message: Option<String>,
    pub updated_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct LifecycleEntry {
    pub event_id: String,
    pub event_type: String,
    pub event_time_unix_nanos: UnixNanos,
    pub record_kind: Option<String>,
    pub record_id: Option<String>,
    #[serde(default)]
    pub operation: Option<String>,
    #[serde(default)]
    pub provenance: Option<String>,
    #[serde(default)]
    pub conflict_policy: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReferenceProjectionSnapshot {
    pub actor_id: ActorId,
    pub workspace_id: WorkspaceId,
    pub launch_id: Option<LaunchId>,
    pub instance_id: Option<InstanceId>,
    pub generation: Generation,
    pub event_sequence: Sequence,
    pub entities: Vec<Entity>,
    pub assets: Vec<Asset>,
    pub instruments: Vec<Instrument>,
    pub listings: Vec<Listing>,
    pub markets: Vec<Market>,
    pub provider_health: Vec<ProviderHealthState>,
    pub option_underlyings: Vec<InstrumentId>,
    pub lifecycle_events: Vec<LifecycleEntry>,
}

impl Default for ReferenceProjectionSnapshot {
    fn default() -> Self {
        Self {
            actor_id: ActorId::new("reference:unscoped").expect("valid reference actor"),
            workspace_id: WorkspaceId::new("workspace:unscoped").expect("valid workspace"),
            launch_id: None,
            instance_id: None,
            generation: Generation::default(),
            event_sequence: Sequence::default(),
            entities: Vec::new(),
            assets: Vec::new(),
            instruments: Vec::new(),
            listings: Vec::new(),
            markets: Vec::new(),
            provider_health: Vec::new(),
            option_underlyings: Vec::new(),
            lifecycle_events: Vec::new(),
        }
    }
}

impl ReferenceProjectionSnapshot {
    /// Projection consumed by Market. Operational health, history and
    /// unrelated catalog records are deliberately excluded.
    pub fn market_projection(&self) -> Self {
        let markets = self
            .markets
            .iter()
            .filter(|value| active(value.status))
            .cloned()
            .collect::<Vec<_>>();
        let instrument_ids = markets
            .iter()
            .map(|value| value.instrument_id.clone())
            .collect::<std::collections::BTreeSet<_>>();
        self.projection_base(
            self.instruments
                .iter()
                .filter(|value| instrument_ids.contains(value.instrument_id.as_str()))
                .cloned()
                .collect(),
            markets,
        )
    }

    /// Projection consumed by Execution for admission and provider address
    /// resolution.
    pub fn execution_projection(&self) -> Self {
        let markets = self
            .markets
            .iter()
            .filter(|value| active(value.status))
            .cloned()
            .collect::<Vec<_>>();
        let instruments = self
            .instruments
            .iter()
            .filter(|value| active(value.status))
            .cloned()
            .collect();
        self.projection_base(instruments, markets)
    }

    /// Projection consumed by Account to map provider observations to
    /// canonical instrument and market identity.
    pub fn account_projection(&self) -> Self {
        let markets = self
            .markets
            .iter()
            .filter(|value| active(value.status))
            .cloned()
            .collect::<Vec<_>>();
        let instrument_ids = markets
            .iter()
            .map(|value| value.instrument_id.clone())
            .collect::<std::collections::BTreeSet<_>>();
        self.projection_base(
            self.instruments
                .iter()
                .filter(|value| instrument_ids.contains(value.instrument_id.as_str()))
                .cloned()
                .collect(),
            markets,
        )
    }

    fn projection_base(&self, instruments: Vec<Instrument>, markets: Vec<Market>) -> Self {
        Self {
            actor_id: self.actor_id.clone(),
            workspace_id: self.workspace_id.clone(),
            launch_id: self.launch_id.clone(),
            instance_id: self.instance_id.clone(),
            generation: self.generation,
            event_sequence: self.event_sequence,
            instruments,
            markets,
            ..Default::default()
        }
    }
}

fn active(status: ReferenceStatus) -> bool {
    matches!(status, ReferenceStatus::Active | ReferenceStatus::Trading)
}
