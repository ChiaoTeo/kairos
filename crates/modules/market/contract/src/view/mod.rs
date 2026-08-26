mod key;

use std::path::{Path, PathBuf};

use kairos_indexed_view::{
    EnvironmentOptions, IndexedViewIdentity, IndexedViewReader, MetadataSnapshot, SchemaDescriptor,
    SchemaSet, environment_path,
};
use kairos_primitives::runtime::InstanceIdentity;
use kairos_protocol::generated::kairos::market::v_2 as fb;
pub use key::{MarketViewKey, MarketViewKind};

use crate::{ContractError, ContractResult};

pub const MARKET_QUOTES_DATABASE: &str = "quotes";
pub const MARKET_BARS_DATABASE: &str = "bars";
pub const MARKET_GREEKS_DATABASE: &str = "greeks";
pub const MARKET_RATES_DATABASE: &str = "rates";
pub const MARKET_TICKERS_DATABASE: &str = "tickers_24h";
pub const MARKET_MARK_PRICES_DATABASE: &str = "mark_prices";
pub const MARKET_FUNDING_RATES_DATABASE: &str = "funding_rates";
pub const MARKET_OPEN_INTEREST_DATABASE: &str = "open_interest";
pub const MARKET_INDEX_PRICES_DATABASE: &str = "index_prices";
pub const MARKET_ORDER_BOOKS_DATABASE: &str = "order_books";
pub const MARKET_FRESHNESS_DATABASE: &str = "freshness";
pub const MARKET_RESOURCE_EPOCH: u64 = 1;
pub const MARKET_MAP_SIZE: usize = 512 * 1024 * 1024;
const KEY_VERSION: u8 = 1;

const DATABASES: [&str; 11] = [
    MARKET_QUOTES_DATABASE,
    MARKET_BARS_DATABASE,
    MARKET_GREEKS_DATABASE,
    MARKET_RATES_DATABASE,
    MARKET_TICKERS_DATABASE,
    MARKET_MARK_PRICES_DATABASE,
    MARKET_FUNDING_RATES_DATABASE,
    MARKET_OPEN_INTEREST_DATABASE,
    MARKET_INDEX_PRICES_DATABASE,
    MARKET_ORDER_BOOKS_DATABASE,
    MARKET_FRESHNESS_DATABASE,
];

pub fn market_indexed_schema_set() -> SchemaSet {
    SchemaSet::new(DATABASES.map(|database| {
        SchemaDescriptor::new(
            database,
            1,
            match database {
                MARKET_QUOTES_DATABASE => "MQC3",
                MARKET_BARS_DATABASE => "MBC3",
                MARKET_GREEKS_DATABASE => "MGC3",
                MARKET_RATES_DATABASE => "MRC3",
                MARKET_TICKERS_DATABASE => "MTC3",
                MARKET_MARK_PRICES_DATABASE => "MMP3",
                MARKET_FUNDING_RATES_DATABASE => "MFR3",
                MARKET_OPEN_INTEREST_DATABASE => "MOI3",
                MARKET_INDEX_PRICES_DATABASE => "MIP3",
                MARKET_ORDER_BOOKS_DATABASE => "MOB3",
                MARKET_FRESHNESS_DATABASE => "MFS3",
                _ => unreachable!("all Market databases have a dedicated value root"),
            },
            1,
        )
        .expect("static Market schema")
    }))
    .expect("static Market databases are unique")
}

pub fn market_indexed_identity(
    identity: &InstanceIdentity,
    producer_incarnation: u64,
) -> IndexedViewIdentity {
    IndexedViewIdentity::new(
        identity.workspace_id.to_string(),
        identity.launch_id().map(ToString::to_string),
        identity.instance_id().map(ToString::to_string),
        "Market",
        "market-main",
        MARKET_RESOURCE_EPOCH,
        producer_incarnation,
        market_indexed_schema_set(),
    )
    .expect("validated Market identity produces indexed-view identity")
}

pub fn market_indexed_environment_path(
    root: impl AsRef<Path>,
    identity: &InstanceIdentity,
) -> ContractResult<PathBuf> {
    environment_path(root, &market_indexed_identity(identity, 1))
        .map_err(|error| ContractError::Transport(error.to_string()))
}

pub fn market_indexed_key(key: &MarketViewKey) -> ContractResult<Vec<u8>> {
    let parts = [
        key.scope_key.as_str(),
        key.provider.as_str(),
        key.qualifier.as_deref().unwrap_or(""),
    ];
    let mut encoded = vec![KEY_VERSION];
    for part in parts {
        if part.trim() != part || part.as_bytes().contains(&0) {
            return Err(ContractError::Invalid(
                "invalid Market indexed key component".into(),
            ));
        }
        let length: u16 = part.len().try_into().map_err(|_| {
            ContractError::Invalid("Market indexed key component is too long".into())
        })?;
        encoded.extend_from_slice(&length.to_be_bytes());
        encoded.extend_from_slice(part.as_bytes());
    }
    Ok(encoded)
}

pub fn market_database(kind: &MarketViewKind) -> &'static str {
    match kind {
        MarketViewKind::Quote => MARKET_QUOTES_DATABASE,
        MarketViewKind::Bar => MARKET_BARS_DATABASE,
        MarketViewKind::Greeks => MARKET_GREEKS_DATABASE,
        MarketViewKind::Rate => MARKET_RATES_DATABASE,
        MarketViewKind::Ticker24h => MARKET_TICKERS_DATABASE,
        MarketViewKind::MarkPrice => MARKET_MARK_PRICES_DATABASE,
        MarketViewKind::FundingRate => MARKET_FUNDING_RATES_DATABASE,
        MarketViewKind::OpenInterest => MARKET_OPEN_INTEREST_DATABASE,
        MarketViewKind::IndexPrice => MARKET_INDEX_PRICES_DATABASE,
        MarketViewKind::OrderBook => MARKET_ORDER_BOOKS_DATABASE,
        MarketViewKind::Freshness => MARKET_FRESHNESS_DATABASE,
    }
}

pub struct MarketIndexedView {
    reader: IndexedViewReader,
}

impl MarketIndexedView {
    pub fn open(root: impl AsRef<Path>, identity: &InstanceIdentity) -> ContractResult<Self> {
        let options = EnvironmentOptions::new(
            market_indexed_environment_path(root, identity)?,
            MARKET_MAP_SIZE,
        )
        .map_err(|error| ContractError::Transport(error.to_string()))?;
        let reader = IndexedViewReader::open(&options, market_indexed_identity(identity, 1))
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        Ok(Self { reader })
    }

    pub fn metadata(&self) -> ContractResult<MetadataSnapshot> {
        self.reader
            .metadata()
            .map_err(|error| ContractError::Transport(error.to_string()))
    }

    pub fn get(&self, key: &MarketViewKey) -> ContractResult<Option<MarketIndexedSnapshot>> {
        let snapshot = self
            .reader
            .value_snapshot(market_database(&key.kind), &market_indexed_key(key)?)
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        if snapshot.metadata.rebuild_state != kairos_indexed_view::RebuildState::Ready {
            return Err(ContractError::Transport(
                "Market indexed current view is not ready".into(),
            ));
        }
        snapshot
            .value
            .map(|bytes| MarketIndexedSnapshot::new(snapshot.metadata, key.clone(), bytes))
            .transpose()
    }
}

pub struct MarketIndexedSnapshot {
    metadata: MetadataSnapshot,
    key: MarketViewKey,
    bytes: Vec<u8>,
}

pub enum MarketIndexedValue<'a> {
    Quote(fb::Quote<'a>),
    Bar(fb::Bar<'a>),
    Greeks(fb::Greeks<'a>),
    Rate(fb::Rate<'a>),
    Ticker24h(fb::Ticker24h<'a>),
    MarkPrice(fb::MarkPrice<'a>),
    FundingRate(fb::FundingRate<'a>),
    OpenInterest(fb::OpenInterest<'a>),
    IndexPrice(fb::IndexPrice<'a>),
    OrderBook(fb::OrderBookSnapshotValue<'a>),
    Freshness(fb::FreshnessEntry<'a>),
}

macro_rules! market_value_accessors {
    ($($method:ident => $variant:ident($value:ty)),+ $(,)?) => {
        impl<'a> MarketIndexedValue<'a> {
            $(
                pub fn $method(&self) -> Option<$value> {
                    match self {
                        Self::$variant(value) => Some(*value),
                        _ => None,
                    }
                }
            )+
        }
    };
}

market_value_accessors! {
    quote => Quote(fb::Quote<'a>),
    bar => Bar(fb::Bar<'a>),
    greeks => Greeks(fb::Greeks<'a>),
    rate => Rate(fb::Rate<'a>),
    ticker_24h => Ticker24h(fb::Ticker24h<'a>),
    mark_price => MarkPrice(fb::MarkPrice<'a>),
    funding_rate => FundingRate(fb::FundingRate<'a>),
    open_interest => OpenInterest(fb::OpenInterest<'a>),
    index_price => IndexPrice(fb::IndexPrice<'a>),
    order_book => OrderBook(fb::OrderBookSnapshotValue<'a>),
    freshness => Freshness(fb::FreshnessEntry<'a>),
}

fn validate_current_identity(
    identity: fb::MarketCurrentIdentity<'_>,
    key: &MarketViewKey,
) -> ContractResult<()> {
    if identity.scope_key() != key.scope_key
        || identity.provider() != key.provider.as_str()
        || identity.qualifier().unwrap_or("") != key.qualifier.as_deref().unwrap_or("")
    {
        return Err(ContractError::Invalid(
            "Market indexed key/value identity mismatch".into(),
        ));
    }
    Ok(())
}

macro_rules! validate_current_root {
    ($bytes:expr, $key:expr, $identifier:ident, $decode:ident, $label:literal) => {{
        if !fb::$identifier($bytes) {
            return Err(ContractError::Invalid(concat!("expected ", $label).into()));
        }
        let root =
            fb::$decode($bytes).map_err(|error| ContractError::Invalid(error.to_string()))?;
        validate_current_identity(root.identity(), $key)
    }};
}

impl MarketIndexedSnapshot {
    fn new(metadata: MetadataSnapshot, key: MarketViewKey, bytes: Vec<u8>) -> ContractResult<Self> {
        match key.kind {
            MarketViewKind::Quote => validate_current_root!(
                &bytes,
                &key,
                market_quote_current_buffer_has_identifier,
                root_as_market_quote_current,
                "MQC3 MarketQuoteCurrent"
            )?,
            MarketViewKind::Bar => validate_current_root!(
                &bytes,
                &key,
                market_bar_current_buffer_has_identifier,
                root_as_market_bar_current,
                "MBC3 MarketBarCurrent"
            )?,
            MarketViewKind::Greeks => validate_current_root!(
                &bytes,
                &key,
                market_greeks_current_buffer_has_identifier,
                root_as_market_greeks_current,
                "MGC3 MarketGreeksCurrent"
            )?,
            MarketViewKind::Rate => validate_current_root!(
                &bytes,
                &key,
                market_rate_current_buffer_has_identifier,
                root_as_market_rate_current,
                "MRC3 MarketRateCurrent"
            )?,
            MarketViewKind::Ticker24h => validate_current_root!(
                &bytes,
                &key,
                market_ticker_24h_current_buffer_has_identifier,
                root_as_market_ticker_24h_current,
                "MTC3 MarketTicker24hCurrent"
            )?,
            MarketViewKind::MarkPrice => validate_current_root!(
                &bytes,
                &key,
                market_mark_price_current_buffer_has_identifier,
                root_as_market_mark_price_current,
                "MMP3 MarketMarkPriceCurrent"
            )?,
            MarketViewKind::FundingRate => validate_current_root!(
                &bytes,
                &key,
                market_funding_rate_current_buffer_has_identifier,
                root_as_market_funding_rate_current,
                "MFR3 MarketFundingRateCurrent"
            )?,
            MarketViewKind::OpenInterest => validate_current_root!(
                &bytes,
                &key,
                market_open_interest_current_buffer_has_identifier,
                root_as_market_open_interest_current,
                "MOI3 MarketOpenInterestCurrent"
            )?,
            MarketViewKind::IndexPrice => validate_current_root!(
                &bytes,
                &key,
                market_index_price_current_buffer_has_identifier,
                root_as_market_index_price_current,
                "MIP3 MarketIndexPriceCurrent"
            )?,
            MarketViewKind::OrderBook => validate_current_root!(
                &bytes,
                &key,
                market_order_book_current_buffer_has_identifier,
                root_as_market_order_book_current,
                "MOB3 MarketOrderBookCurrent"
            )?,
            MarketViewKind::Freshness => validate_current_root!(
                &bytes,
                &key,
                market_freshness_current_buffer_has_identifier,
                root_as_market_freshness_current,
                "MFS3 MarketFreshnessCurrent"
            )?,
        }
        Ok(Self {
            metadata,
            key,
            bytes,
        })
    }

    pub fn metadata(&self) -> &MetadataSnapshot {
        &self.metadata
    }
    pub fn key(&self) -> &MarketViewKey {
        &self.key
    }
    pub fn source_event_id(&self) -> ContractResult<Option<&str>> {
        let invalid =
            |error: flatbuffers::InvalidFlatbuffer| ContractError::Invalid(error.to_string());
        Ok(match self.key.kind {
            MarketViewKind::Quote => fb::root_as_market_quote_current(&self.bytes)
                .map_err(invalid)?
                .identity()
                .source_event_id(),
            MarketViewKind::Bar => fb::root_as_market_bar_current(&self.bytes)
                .map_err(invalid)?
                .identity()
                .source_event_id(),
            MarketViewKind::Greeks => fb::root_as_market_greeks_current(&self.bytes)
                .map_err(invalid)?
                .identity()
                .source_event_id(),
            MarketViewKind::Rate => fb::root_as_market_rate_current(&self.bytes)
                .map_err(invalid)?
                .identity()
                .source_event_id(),
            MarketViewKind::Ticker24h => fb::root_as_market_ticker_24h_current(&self.bytes)
                .map_err(invalid)?
                .identity()
                .source_event_id(),
            MarketViewKind::MarkPrice => fb::root_as_market_mark_price_current(&self.bytes)
                .map_err(invalid)?
                .identity()
                .source_event_id(),
            MarketViewKind::FundingRate => fb::root_as_market_funding_rate_current(&self.bytes)
                .map_err(invalid)?
                .identity()
                .source_event_id(),
            MarketViewKind::OpenInterest => fb::root_as_market_open_interest_current(&self.bytes)
                .map_err(invalid)?
                .identity()
                .source_event_id(),
            MarketViewKind::IndexPrice => fb::root_as_market_index_price_current(&self.bytes)
                .map_err(invalid)?
                .identity()
                .source_event_id(),
            MarketViewKind::OrderBook => fb::root_as_market_order_book_current(&self.bytes)
                .map_err(invalid)?
                .identity()
                .source_event_id(),
            MarketViewKind::Freshness => fb::root_as_market_freshness_current(&self.bytes)
                .map_err(invalid)?
                .identity()
                .source_event_id(),
        })
    }
    pub fn value(&self) -> ContractResult<MarketIndexedValue<'_>> {
        let invalid =
            |error: flatbuffers::InvalidFlatbuffer| ContractError::Invalid(error.to_string());
        Ok(match self.key.kind {
            MarketViewKind::Quote => MarketIndexedValue::Quote(
                fb::root_as_market_quote_current(&self.bytes)
                    .map_err(invalid)?
                    .value(),
            ),
            MarketViewKind::Bar => MarketIndexedValue::Bar(
                fb::root_as_market_bar_current(&self.bytes)
                    .map_err(invalid)?
                    .value(),
            ),
            MarketViewKind::Greeks => MarketIndexedValue::Greeks(
                fb::root_as_market_greeks_current(&self.bytes)
                    .map_err(invalid)?
                    .value(),
            ),
            MarketViewKind::Rate => MarketIndexedValue::Rate(
                fb::root_as_market_rate_current(&self.bytes)
                    .map_err(invalid)?
                    .value(),
            ),
            MarketViewKind::Ticker24h => MarketIndexedValue::Ticker24h(
                fb::root_as_market_ticker_24h_current(&self.bytes)
                    .map_err(invalid)?
                    .value(),
            ),
            MarketViewKind::MarkPrice => MarketIndexedValue::MarkPrice(
                fb::root_as_market_mark_price_current(&self.bytes)
                    .map_err(invalid)?
                    .value(),
            ),
            MarketViewKind::FundingRate => MarketIndexedValue::FundingRate(
                fb::root_as_market_funding_rate_current(&self.bytes)
                    .map_err(invalid)?
                    .value(),
            ),
            MarketViewKind::OpenInterest => MarketIndexedValue::OpenInterest(
                fb::root_as_market_open_interest_current(&self.bytes)
                    .map_err(invalid)?
                    .value(),
            ),
            MarketViewKind::IndexPrice => MarketIndexedValue::IndexPrice(
                fb::root_as_market_index_price_current(&self.bytes)
                    .map_err(invalid)?
                    .value(),
            ),
            MarketViewKind::OrderBook => MarketIndexedValue::OrderBook(
                fb::root_as_market_order_book_current(&self.bytes)
                    .map_err(invalid)?
                    .value(),
            ),
            MarketViewKind::Freshness => MarketIndexedValue::Freshness(
                fb::root_as_market_freshness_current(&self.bytes)
                    .map_err(invalid)?
                    .value(),
            ),
        })
    }
}
