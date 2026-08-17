//! Public Reference models used by SQLite payloads and change events.

use kairos_primitives::{AssetClass, InstrumentKind};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Entity {
    pub entity_id: String,
    pub entity_type: String,
    pub name: String,
    pub status: String,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Asset {
    pub asset_id: String,
    pub code: String,
    pub name: Option<String>,
    pub asset_class: AssetClass,
    pub status: String,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Instrument {
    pub instrument_id: String,
    pub symbol: String,
    pub name: Option<String>,
    pub instrument_type: InstrumentKind,
    /// Legacy wire/persistence slot retained while v2 readers migrate. New
    /// Reference records never populate a second canonical classification.
    #[serde(default)]
    pub product_family: Option<String>,
    pub issuer_id: Option<String>,
    pub share_class: Option<String>,
    pub primary_currency_asset_id: Option<String>,
    pub underlying_instrument_id: Option<String>,
    pub expiry_unix_nanos: Option<u64>,
    pub strike: Option<String>,
    pub option_right: Option<String>,
    pub status: String,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Listing {
    pub listing_id: String,
    pub instrument_id: String,
    pub exchange_id: String,
    pub exchange_symbol: String,
    pub status: String,
    pub effective_from_unix_nanos: u64,
    pub effective_to_unix_nanos: Option<u64>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Market {
    pub market_id: String,
    pub instrument_id: String,
    pub listing_id: Option<String>,
    pub exchange_id: String,
    pub instrument_kind: InstrumentKind,
    pub asset_type: Option<AssetClass>,
    pub underlying_instrument_id: Option<String>,
    pub venue_symbol: Option<String>,
    pub base_asset_id: Option<String>,
    pub quote_asset_id: Option<String>,
    pub status: String,
    pub price_tick: Option<String>,
    pub quantity_tick: Option<String>,
    pub price_precision: i32,
    pub quantity_precision: i32,
    pub minimum_quantity: Option<String>,
    pub minimum_notional: Option<String>,
    pub contract_size: Option<String>,
    pub effective_from_unix_nanos: u64,
    pub effective_to_unix_nanos: Option<u64>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct ProviderHealthState {
    pub provider_id: String,
    pub status: String,
    pub message: Option<String>,
    pub updated_at_unix_nanos: u64,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct LifecycleEntry {
    pub event_id: String,
    pub event_type: String,
    pub event_time_unix_nanos: u64,
    pub record_kind: Option<String>,
    pub record_id: Option<String>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ReferenceProjectionSnapshot {
    pub actor_id: String,
    pub workspace_id: String,
    pub launch_id: Option<String>,
    pub instance_id: Option<String>,
    pub generation: u64,
    pub event_sequence: u64,
    pub entities: Vec<Entity>,
    pub assets: Vec<Asset>,
    pub instruments: Vec<Instrument>,
    pub listings: Vec<Listing>,
    pub markets: Vec<Market>,
    pub provider_health: Vec<ProviderHealthState>,
    pub option_underlyings: Vec<String>,
    pub lifecycle_events: Vec<LifecycleEntry>,
}

impl ReferenceProjectionSnapshot {
    /// Projection consumed by Market. Operational health, history and
    /// unrelated catalog records are deliberately excluded.
    pub fn market_projection(&self) -> Self {
        let markets = self
            .markets
            .iter()
            .filter(|value| active(&value.status))
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
            .filter(|value| active(&value.status))
            .cloned()
            .collect::<Vec<_>>();
        let instruments = self
            .instruments
            .iter()
            .filter(|value| active(&value.status))
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
            .filter(|value| active(&value.status))
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

fn active(status: &str) -> bool {
    matches!(status, "active" | "trading")
}
