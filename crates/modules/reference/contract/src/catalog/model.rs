//! Public Reference catalog models used by SQLite payloads and change events.

use kairos_primitives::decimal::{Money, Price, Quantity};
use kairos_primitives::market::Provider;
use kairos_primitives::reference::{
    AssetClass, AssetId, ExchangeId, InstrumentId, InstrumentKind, IssuerId, JurisdictionCode,
    ListingId, MarketId, MarketSegmentId, Mic, ReferenceCoverageId, ReferenceSourceId,
    ReferenceStatus, Symbol, TradingCalendarId, TradingSessionId, VenueId,
};
use kairos_primitives::time::{Generation, Sequence, UnixNanos};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Exchange {
    pub exchange_id: ExchangeId,
    pub name: String,
    pub status: ReferenceStatus,
}

#[derive(Clone, Copy, Debug, Default, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum VenueKind {
    RegulatedExchange,
    RegulatedMarket,
    TradingPlatform,
    Ats,
    Pts,
    OtcFacility,
    Dealer,
    TradeReportingFacility,
    #[default]
    Unknown,
}

impl VenueKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::RegulatedExchange => "regulated_exchange",
            Self::RegulatedMarket => "regulated_market",
            Self::TradingPlatform => "trading_platform",
            Self::Ats => "ats",
            Self::Pts => "pts",
            Self::OtcFacility => "otc_facility",
            Self::Dealer => "dealer",
            Self::TradeReportingFacility => "trade_reporting_facility",
            Self::Unknown => "unknown",
        }
    }
}

impl std::fmt::Display for VenueKind {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(self.as_str())
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum VenueRole {
    Listing,
    Execution,
    Reporting,
}

impl VenueRole {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Listing => "listing",
            Self::Execution => "execution",
            Self::Reporting => "reporting",
        }
    }
}

/// A canonical place or facility that can list, execute, or report trades.
/// Providers are not venues unless they actually operate such a facility.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Venue {
    pub venue_id: VenueId,
    pub name: String,
    pub venue_kind: VenueKind,
    pub roles: std::collections::BTreeSet<VenueRole>,
    pub mic: Option<Mic>,
    pub operating_mic: Option<Mic>,
    pub parent_venue_id: Option<VenueId>,
    pub jurisdiction: Option<JurisdictionCode>,
    pub status: ReferenceStatus,
}

/// The provider-native namespace in which one raw venue identifier appears.
/// Trade and quote exchange identifiers intentionally share one namespace;
/// reporting-facility identifiers do not.
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum VenueIdentifierKind {
    Exchange,
    ReportingFacility,
    Mic,
}

impl VenueIdentifierKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Exchange => "exchange",
            Self::ReportingFacility => "reporting_facility",
            Self::Mic => "mic",
        }
    }
}

/// A Reference-owned mapping from provider-native observation evidence to a
/// canonical venue. It does not assert that any instrument is listed there.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct VenueIdentifierMapping {
    pub source_id: ReferenceSourceId,
    pub provider: Provider,
    pub provider_product: String,
    pub identifier_kind: VenueIdentifierKind,
    pub identifier: String,
    pub venue_id: VenueId,
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
    #[serde(default)]
    pub settlement_asset_id: Option<AssetId>,
    pub underlying_instrument_id: Option<InstrumentId>,
    pub expiry_unix_nanos: Option<UnixNanos>,
    pub strike: Option<Price>,
    pub option_right: Option<String>,
    pub status: ReferenceStatus,
}

/// One provider catalog's committed claim that it currently offers an
/// instrument. This is deliberately separate from canonical listings and
/// markets: a broker product can offer AAPL without being AAPL's listing
/// exchange or an exchange-operated market.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReferenceInstrumentAvailability {
    pub source_id: ReferenceSourceId,
    pub instrument: Instrument,
}

/// A provider catalog's claim that it currently contains an instrument. It
/// does not imply a listing, an execution venue, or an installed runtime route.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ProviderCatalogMembership {
    pub source_id: ReferenceSourceId,
    pub instrument_id: InstrumentId,
    pub provider_symbol: Option<String>,
    pub provider_product: Option<String>,
    pub status: ReferenceStatus,
    pub effective_from_unix_nanos: UnixNanos,
    pub effective_to_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReferenceFactKind {
    Venue,
    Asset,
    Instrument,
    Listing,
    Market,
    ProviderCatalogMembership,
    VenueIdentifierMapping,
    TradingRules,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ReferenceCoverageScope {
    ProviderCatalog {
        binding: crate::ReferenceSourceBinding,
    },
    VenueListings {
        venue_ids: Vec<VenueId>,
        instrument_kind: InstrumentKind,
    },
    VenueMarkets {
        venue_ids: Vec<VenueId>,
        instrument_kind: InstrumentKind,
    },
    UnderlyingOptions {
        underlying_instrument_ids: Vec<InstrumentId>,
    },
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CoverageCompleteness {
    #[default]
    Unknown,
    Partial,
    CompleteForDeclaredScope,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CoverageState {
    #[default]
    NotConfigured,
    Waiting,
    Scanning,
    Promoting,
    Usable,
    Stale,
    RetryWaiting,
    Paused,
    Unavailable,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ReferenceCoverage {
    pub coverage_id: ReferenceCoverageId,
    pub source_id: ReferenceSourceId,
    pub fact_kinds: std::collections::BTreeSet<ReferenceFactKind>,
    pub scope: ReferenceCoverageScope,
    pub completeness: CoverageCompleteness,
    pub state: CoverageState,
    pub generation: Option<Generation>,
    pub event_sequence: Option<Sequence>,
    pub last_attempt_unix_nanos: Option<UnixNanos>,
    pub last_success_unix_nanos: Option<UnixNanos>,
    pub stale_after_unix_nanos: Option<UnixNanos>,
    pub has_last_known_good: bool,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReferenceKnowledgeConclusion {
    Found,
    NotFoundInCoveredScope,
    #[default]
    UnknownOutsideCoverage,
    Preparing,
    KnownButStale,
    SourceUnavailable,
}

#[derive(Clone, Copy, Debug, Default, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ListingRole {
    Primary,
    Secondary,
    CrossListing,
    AdmissionWithoutPrimaryDesignation,
    #[default]
    Unknown,
}

impl ListingRole {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Primary => "primary",
            Self::Secondary => "secondary",
            Self::CrossListing => "cross_listing",
            Self::AdmissionWithoutPrimaryDesignation => "admission_without_primary_designation",
            Self::Unknown => "unknown",
        }
    }
}

impl std::fmt::Display for ListingRole {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(self.as_str())
    }
}

/// v3 listing semantics: formal admission belongs to a listing venue and is
/// independent from the facilities where trades execute.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct VenueListing {
    pub listing_id: ListingId,
    pub instrument_id: InstrumentId,
    pub listing_venue_id: VenueId,
    pub market_segment_id: Option<MarketSegmentId>,
    pub listing_symbol: Symbol,
    pub listing_role: ListingRole,
    pub status: ReferenceStatus,
    pub effective_from_unix_nanos: UnixNanos,
    pub effective_to_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct TradingRules {
    pub price_tick: Option<Price>,
    pub quantity_tick: Option<Quantity>,
    pub price_precision: i32,
    pub quantity_precision: i32,
    pub minimum_quantity: Option<Quantity>,
    pub minimum_notional: Option<Money>,
    pub contract_size: Option<Quantity>,
}

/// v3 market semantics: one independently addressable liquidity or execution
/// entry point. A broker SOR and a data-vendor route are not canonical markets.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct VenueMarket {
    pub market_id: MarketId,
    pub instrument_id: InstrumentId,
    pub execution_venue_id: VenueId,
    pub origin_listing_id: Option<ListingId>,
    pub market_segment_id: Option<MarketSegmentId>,
    pub venue_symbol: Option<Symbol>,
    pub trading_calendar_id: Option<TradingCalendarId>,
    pub trading_session_ids: Vec<TradingSessionId>,
    pub base_asset_id: Option<AssetId>,
    pub quote_asset_id: Option<AssetId>,
    pub status: ReferenceStatus,
    pub trading_rules: TradingRules,
    pub effective_from_unix_nanos: UnixNanos,
    pub effective_to_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct Listing {
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
    pub listing_id: Option<ListingId>,
    pub exchange_id: ExchangeId,
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

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ProviderHealthState {
    pub provider_id: Provider,
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
