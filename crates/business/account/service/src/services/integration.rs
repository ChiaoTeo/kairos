//! Account-owned mapping of normalized Integration facts.
//!
//! Integration already defines the connection capabilities. Account keeps
//! concrete dependency holders here instead of mirroring those capabilities
//! with another public protocol hierarchy.

use std::collections::BTreeMap;
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use crate::application::{AccountMarketProfile, AccountMarketProfileRequest};
use crate::domain::{
    AccountEvent, AccountModel, AccountObservedFill, AccountOrderObservation, AccountSegment,
    AccountSnapshot, AccountStatus, AssetId, Balance, FillId, InstrumentId, MarginMode, Money,
    OpenOrder, Position, PositionMode, SegmentKey, SignedQuantity,
};
use kairos_integration::application::{
    AsyncAccountEventSource, AsyncAccountMarketProfileConnection, AsyncAccountReadConnection,
    ExternalMarketProfile, ExternalMarketProfileRequest, IntegrationError,
};
use kairos_integration::blocking::{AccountMarketProfileConnection, AccountReadConnection};

use futures_util::{stream::FuturesUnordered, StreamExt};

use crate::services::refresh::RefreshFetch;

const ASYNC_ACCOUNT_QUERY_TIMEOUT: Duration = Duration::from_secs(30);
const ASYNC_ACCOUNT_CIRCUIT_FAILURE_THRESHOLD: u32 = 3;
const ASYNC_ACCOUNT_CIRCUIT_COOLDOWN: Duration = Duration::from_secs(30);

#[derive(Clone, Default)]
pub(crate) struct AccountInstrumentResolver {
    reader: Option<Arc<kairos_reference_contract::ReferenceSqliteReader>>,
    cache: Arc<Mutex<BTreeMap<String, (InstrumentId, Option<kairos_domain_types::MarketId>)>>>,
    cache_generation: Arc<Mutex<Option<u64>>>,
    #[cfg(test)]
    fixture_markets: Arc<Vec<kairos_reference_contract::ReferenceMarket>>,
    #[cfg(test)]
    fixture_instruments: Arc<Vec<kairos_reference_contract::model::Instrument>>,
}

impl AccountInstrumentResolver {
    pub(crate) fn from_reference_database(path: impl AsRef<Path>) -> Result<Self, String> {
        Ok(Self {
            reader: Some(Arc::new(
                kairos_reference_contract::ReferenceSqliteReader::open(path)
                    .map_err(|error| error.to_string())?,
            )),
            ..Default::default()
        })
    }

    fn resolve(
        &self,
        provider: &kairos_integration::application::ProviderInstrumentRef,
    ) -> Result<(InstrumentId, Option<kairos_domain_types::MarketId>), String> {
        let key = format!(
            "{}|{}|{}",
            provider.participant.id.to_ascii_lowercase(),
            provider
                .instrument_type
                .as_ref()
                .map(|value| value.as_str())
                .unwrap_or_default()
                .to_ascii_lowercase(),
            provider.source_symbol.as_str().to_ascii_uppercase()
        );
        if let Some(reader) = &self.reader {
            let generation = reader
                .watermark()
                .map_err(|error| error.to_string())?
                .generation;
            let mut cached_generation = self
                .cache_generation
                .lock()
                .map_err(|_| "Reference identity cache generation lock poisoned".to_string())?;
            if *cached_generation != Some(generation) {
                self.cache
                    .lock()
                    .map_err(|_| "Reference identity cache lock poisoned".to_string())?
                    .clear();
                *cached_generation = Some(generation);
            }
            if let Some(value) = self
                .cache
                .lock()
                .map_err(|_| "Reference identity cache lock poisoned".to_string())?
                .get(&key)
                .cloned()
            {
                return Ok(value);
            }
        }

        let resolved = self.resolve_uncached(provider)?;
        if self.reader.is_some() {
            self.cache
                .lock()
                .map_err(|_| "Reference identity cache lock poisoned".to_string())?
                .insert(key, resolved.clone());
        }
        Ok(resolved)
    }

    fn resolve_uncached(
        &self,
        provider: &kairos_integration::application::ProviderInstrumentRef,
    ) -> Result<(InstrumentId, Option<kairos_domain_types::MarketId>), String> {
        if let Some(access_id) = provider.market_data_access_id.as_deref() {
            let reader = self
                .reader
                .as_ref()
                .ok_or_else(|| "Reference SQLite reader is not configured".to_string())?;
            let accesses = reader
                .market_data_accesses(&kairos_reference_contract::SqliteMarketDataAccessQuery {
                    access_id: Some(access_id.to_owned()),
                    statuses: vec!["active".into(), "trading".into()],
                    limit: 2,
                    ..Default::default()
                })
                .map_err(|error| error.to_string())?;
            let [access] = accesses.as_slice() else {
                return Err(format!(
                    "Reference has no unique MarketDataAccess {access_id}"
                ));
            };
            let markets = reader
                .markets(&kairos_reference_contract::SqliteMarketQuery {
                    market_id: access.market_id.clone().into(),
                    statuses: vec!["active".into(), "trading".into()],
                    limit: 2,
                    ..Default::default()
                })
                .map_err(|error| error.to_string())?;
            let [market] = markets.as_slice() else {
                return Err(format!("MarketDataAccess {access_id} has no unique Market"));
            };
            return Ok((
                InstrumentId::new(market.instrument_id.clone())
                    .map_err(|error| error.to_string())?,
                Some(kairos_domain_types::MarketId::new(
                    market.market_id.clone(),
                )?),
            ));
        }
        #[cfg(not(test))]
        return Err(
            "provider instrument must carry market_data_access_id; symbol-based identity resolution is disabled"
                .into(),
        );

        #[cfg(test)]
        {
            let symbol = provider.source_symbol.as_str();
            if provider.participant.id.eq_ignore_ascii_case("ibkr") {
                let instruments = self.instruments(symbol, "equity")?;
                let matches = instruments
                    .iter()
                    .filter(|value| {
                        value.symbol.eq_ignore_ascii_case(symbol)
                            && value.instrument_type.eq_ignore_ascii_case("equity")
                            && matches!(value.status.as_str(), "active" | "trading")
                    })
                    .collect::<Vec<_>>();
                let [instrument] = matches.as_slice() else {
                    return Err(identity_resolution_error(provider, matches.len()));
                };
                return Ok((
                    InstrumentId::new(instrument.instrument_id.clone())
                        .map_err(|error| error.to_string())?,
                    None,
                ));
            }

            let domain = provider
                .instrument_type
                .as_ref()
                .map(|value| value.as_str())
                .unwrap_or_default();
            let exchange = format!("exchange:{}", provider.participant.id.to_ascii_lowercase());
            let markets = self.markets(symbol, &exchange)?;
            let matches = markets
                .iter()
                .filter(|value| {
                    value.exchange_id.eq_ignore_ascii_case(&exchange)
                        && value.source_symbol.eq_ignore_ascii_case(symbol)
                        && matches!(value.status.as_str(), "active" | "trading")
                        && provider_domain_matches_market(domain, &value.market_type)
                })
                .collect::<Vec<_>>();
            let [market] = matches.as_slice() else {
                return Err(identity_resolution_error(provider, matches.len()));
            };
            Ok((
                InstrumentId::new(market.instrument_id.clone())
                    .map_err(|error| error.to_string())?,
                Some(kairos_domain_types::MarketId::new(
                    market.market_id.clone(),
                )?),
            ))
        }
    }

    #[cfg(test)]
    fn markets(
        &self,
        source_symbol: &str,
        exchange_id: &str,
    ) -> Result<Vec<kairos_reference_contract::ReferenceMarket>, String> {
        #[cfg(test)]
        if self.reader.is_none() {
            return Ok(self.fixture_markets.as_ref().clone());
        }
        let reader = self
            .reader
            .as_ref()
            .ok_or_else(|| "Reference SQLite reader is not configured".to_string())?;
        reader
            .markets(&kairos_reference_contract::SqliteMarketQuery {
                source_symbol: Some(source_symbol.to_owned()),
                exchange_id: Some(exchange_id.to_owned()),
                statuses: vec!["active".into(), "trading".into()],
                limit: 100,
                ..Default::default()
            })
            .map_err(|error| error.to_string())
    }

    #[cfg(test)]
    fn instruments(
        &self,
        symbol: &str,
        instrument_type: &str,
    ) -> Result<Vec<kairos_reference_contract::model::Instrument>, String> {
        #[cfg(test)]
        if self.reader.is_none() {
            return Ok(self.fixture_instruments.as_ref().clone());
        }
        let reader = self
            .reader
            .as_ref()
            .ok_or_else(|| "Reference SQLite reader is not configured".to_string())?;
        reader
            .instruments(&kairos_reference_contract::SqliteInstrumentQuery {
                symbol: Some(symbol.to_owned()),
                instrument_type: Some(instrument_type.to_owned()),
                statuses: vec!["active".into(), "trading".into()],
                limit: 100,
                ..Default::default()
            })
            .map_err(|error| error.to_string())
    }

    #[cfg(test)]
    fn fixture(
        markets: Vec<kairos_reference_contract::ReferenceMarket>,
        instruments: Vec<kairos_reference_contract::model::Instrument>,
    ) -> Self {
        Self {
            fixture_markets: Arc::new(markets),
            fixture_instruments: Arc::new(instruments),
            ..Default::default()
        }
    }
}

#[cfg(test)]
fn provider_domain_matches_market(domain: &str, market_type: &str) -> bool {
    let domain = domain.to_ascii_lowercase();
    let market_type = market_type.to_ascii_lowercase();
    if domain.contains("spot") || domain.contains("margin") {
        return market_type == "spot";
    }
    if domain.contains("option") {
        return matches!(market_type.as_str(), "option" | "options");
    }
    if domain.contains("future") || domain.contains("swap") {
        return matches!(
            market_type.as_str(),
            "future" | "futures" | "perpetual" | "swap"
        );
    }
    true
}

#[cfg(test)]
fn identity_resolution_error(
    provider: &kairos_integration::application::ProviderInstrumentRef,
    matches: usize,
) -> String {
    format!(
        "Reference identity resolution expected one match for {}/{}/{}, found {matches}",
        provider.participant.id,
        provider
            .instrument_type
            .as_ref()
            .map(|value| value.as_str())
            .unwrap_or("unspecified"),
        provider.source_symbol
    )
}

/// Account-owned heterogeneous holder for concrete Integration event sources.
/// It is a dispatch container, not another implementation of Integration's
/// provider capability trait.
pub(crate) enum AccountAsyncEventSource {
    BinanceSpot {
        binding_id: String,
        source: kairos_integration::participants::binance::BinanceSpotAccountEvents,
    },
    BinanceFutures {
        binding_id: String,
        source: kairos_integration::participants::binance::BinanceFuturesAccountEvents,
    },
    BinanceOptions {
        binding_id: String,
        source: kairos_integration::participants::binance::BinanceOptionsAccountEvents,
    },
    BinanceMargin {
        binding_id: String,
        source: kairos_integration::participants::binance::BinanceMarginAccountEvents,
    },
    Ibkr {
        binding_id: String,
        source: kairos_integration::participants::ibkr::IbkrAccountEvents,
    },
    OkxTrading {
        binding_id: String,
        source: kairos_integration::participants::okx::OkxTradingAccountEvents,
    },
}

/// Concrete async account-read capabilities selected by Account composition.
/// This enum is deliberately private: it keeps heterogeneous provider handles
/// without publishing a second Account-owned provider protocol.
pub(crate) enum AccountAsyncSnapshotConnection {
    BinanceSpot(kairos_integration::participants::binance::BinanceSpotAccountRead),
    BinanceFunding(kairos_integration::participants::binance::BinanceFundingAccountRead),
    BinanceMargin(kairos_integration::participants::binance::BinanceMarginAccountRead),
    BinanceFutures(kairos_integration::participants::binance::BinanceFuturesAccountRead),
    BinanceOptions(kairos_integration::participants::binance::BinanceOptionsAccountRead),
    Ibkr(kairos_integration::participants::ibkr::IbkrAccountRead),
    OkxTrading(kairos_integration::participants::okx::OkxTradingAccountRead),
}

impl AccountAsyncSnapshotConnection {
    async fn fetch_account(
        &mut self,
        segment: &ExternalAccountSegment,
    ) -> Result<
        kairos_integration::application::capabilities::account_facts::ExternalAccountSnapshot,
        IntegrationError,
    > {
        match self {
            Self::BinanceSpot(connection) => connection.fetch_account(segment).await,
            Self::BinanceFunding(connection) => connection.fetch_account(segment).await,
            Self::BinanceMargin(connection) => connection.fetch_account(segment).await,
            Self::BinanceFutures(connection) => connection.fetch_account(segment).await,
            Self::BinanceOptions(connection) => connection.fetch_account(segment).await,
            Self::Ibkr(connection) => connection.fetch_account(segment).await,
            Self::OkxTrading(connection) => connection.fetch_account(segment).await,
        }
    }
}

struct AsyncSnapshotSlot {
    connection: AccountAsyncSnapshotConnection,
    consecutive_failures: u32,
    circuit_open_until: Option<Instant>,
}

/// Account-owned async snapshot bindings. Network futures run directly on the
/// caller's Tokio runtime and unrelated segments are fetched concurrently.
pub(crate) struct AccountAsyncSnapshotGateway {
    connections: BTreeMap<String, AsyncSnapshotSlot>,
    resolver: AccountInstrumentResolver,
}

impl AccountAsyncSnapshotGateway {
    pub(crate) fn new(
        connections: BTreeMap<String, AccountAsyncSnapshotConnection>,
        resolver: AccountInstrumentResolver,
    ) -> Self {
        Self {
            connections: connections
                .into_iter()
                .map(|(key, connection)| {
                    (
                        key,
                        AsyncSnapshotSlot {
                            connection,
                            consecutive_failures: 0,
                            circuit_open_until: None,
                        },
                    )
                })
                .collect(),
            resolver,
        }
    }

    #[cfg(test)]
    pub(crate) fn len(&self) -> usize {
        self.connections.len()
    }

    pub(crate) async fn fetch(&mut self, segments: Vec<AccountSegment>) -> Vec<RefreshFetch> {
        let mut selected = segments
            .into_iter()
            .map(|segment| (segment.segment_key.to_string(), segment))
            .collect::<BTreeMap<_, _>>();
        let mut futures = FuturesUnordered::new();
        let resolver = self.resolver.clone();

        for (key, slot) in &mut self.connections {
            let Some(segment) = selected.remove(key) else {
                continue;
            };
            let resolver = resolver.clone();
            futures.push(async move {
                let started = Instant::now();
                if slot
                    .circuit_open_until
                    .is_some_and(|until| Instant::now() < until)
                {
                    return RefreshFetch {
                        segment,
                        result: Err("account refresh circuit is open".into()),
                        elapsed_ms: 0,
                    };
                }
                slot.circuit_open_until = None;
                let external = external_segment(&segment);
                let result = match tokio::time::timeout(
                    ASYNC_ACCOUNT_QUERY_TIMEOUT,
                    slot.connection.fetch_account(&external),
                )
                .await
                {
                    Ok(result) => result
                        .map_err(|error| error.to_string())
                        .and_then(|value| map_snapshot(value, &resolver)),
                    Err(_) => Err(format!(
                        "account segment refresh timed out after {}ms",
                        ASYNC_ACCOUNT_QUERY_TIMEOUT.as_millis()
                    )),
                };
                if result.is_err() {
                    slot.consecutive_failures = slot.consecutive_failures.saturating_add(1);
                    if slot.consecutive_failures >= ASYNC_ACCOUNT_CIRCUIT_FAILURE_THRESHOLD {
                        slot.circuit_open_until =
                            Some(Instant::now() + ASYNC_ACCOUNT_CIRCUIT_COOLDOWN);
                    }
                } else {
                    slot.consecutive_failures = 0;
                }
                RefreshFetch {
                    segment,
                    result,
                    elapsed_ms: started.elapsed().as_millis() as u64,
                }
            });
        }

        let mut fetches = Vec::new();
        while let Some(fetch) = futures.next().await {
            fetches.push(fetch);
        }
        fetches.extend(selected.into_values().map(|segment| RefreshFetch {
            result: Err(format!(
                "account segment is not configured: {}",
                segment.segment_key
            )),
            segment,
            elapsed_ms: 0,
        }));
        fetches
    }
}

pub(crate) enum AccountAsyncMarketProfileConnection {
    BinanceSpot(kairos_integration::participants::binance::BinanceSpotAccountMarketProfile),
    OkxTrading(kairos_integration::participants::okx::OkxTradingAccountMarketProfile),
}

impl AccountAsyncMarketProfileConnection {
    async fn fetch_market_profile(
        &mut self,
        request: &ExternalMarketProfileRequest,
    ) -> Result<ExternalMarketProfile, IntegrationError> {
        match self {
            Self::BinanceSpot(connection) => connection.fetch_market_profile(request).await,
            Self::OkxTrading(connection) => connection.fetch_market_profile(request).await,
        }
    }
}

pub(crate) struct AccountAsyncMarketProfileGateway {
    connections: BTreeMap<String, AccountAsyncMarketProfileConnection>,
}

impl AccountAsyncMarketProfileGateway {
    pub(crate) fn new(connections: BTreeMap<String, AccountAsyncMarketProfileConnection>) -> Self {
        Self { connections }
    }

    #[cfg(test)]
    pub(crate) fn len(&self) -> usize {
        self.connections.len()
    }

    pub(crate) async fn fetch(
        &mut self,
        request: &AccountMarketProfileRequest,
    ) -> Result<AccountMarketProfile, String> {
        let connection = self
            .connections
            .get_mut(request.segment_key.as_str())
            .ok_or_else(|| format!("account segment is not configured: {}", request.segment_key))?;
        let external_request = ExternalMarketProfileRequest {
            account_id: request.account_id.clone(),
            segment_key: kairos_domain_types::SegmentKey::new(request.segment_key.to_string())
                .map_err(|error| error.to_string())?,
            market_id: request.market_id.clone(),
            source_symbol: request.source_symbol.clone(),
            market_data_access_id: request.market_data_access_id.clone(),
        };
        tokio::time::timeout(
            ASYNC_ACCOUNT_QUERY_TIMEOUT,
            connection.fetch_market_profile(&external_request),
        )
        .await
        .map_err(|_| {
            format!(
                "account market-profile query timed out after {}ms",
                ASYNC_ACCOUNT_QUERY_TIMEOUT.as_millis()
            )
        })?
        .map_err(|error| error.to_string())
        .and_then(map_profile)
    }
}

impl AccountAsyncEventSource {
    pub(crate) fn binding_id(&self) -> &str {
        match self {
            Self::BinanceSpot { binding_id, .. }
            | Self::BinanceFutures { binding_id, .. }
            | Self::BinanceOptions { binding_id, .. }
            | Self::BinanceMargin { binding_id, .. }
            | Self::Ibkr { binding_id, .. }
            | Self::OkxTrading { binding_id, .. } => binding_id,
        }
    }

    pub(crate) async fn connect_channel(&mut self) -> Result<(), IntegrationError> {
        match self {
            Self::BinanceSpot { source, .. } => source.connect_channel().await,
            Self::BinanceFutures { source, .. } => source.connect_channel().await,
            Self::BinanceOptions { source, .. } => source.connect_channel().await,
            Self::BinanceMargin { source, .. } => source.connect_channel().await,
            Self::Ibkr { source, .. } => source.connect_channel().await,
            Self::OkxTrading { source, .. } => source.connect_channel().await,
        }
    }

    pub(crate) fn channel_health(&self) -> kairos_integration::application::ConnectionHealth {
        match self {
            Self::BinanceSpot { source, .. } => source.channel_health(),
            Self::BinanceFutures { source, .. } => source.channel_health(),
            Self::BinanceOptions { source, .. } => source.channel_health(),
            Self::BinanceMargin { source, .. } => source.channel_health(),
            Self::Ibkr { source, .. } => source.channel_health(),
            Self::OkxTrading { source, .. } => source.channel_health(),
        }
    }

    pub(crate) async fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
        match self {
            Self::BinanceSpot { source, .. } => source.disconnect_channel().await,
            Self::BinanceFutures { source, .. } => source.disconnect_channel().await,
            Self::BinanceOptions { source, .. } => source.disconnect_channel().await,
            Self::BinanceMargin { source, .. } => source.disconnect_channel().await,
            Self::Ibkr { source, .. } => source.disconnect_channel().await,
            Self::OkxTrading { source, .. } => source.disconnect_channel().await,
        }
    }

    pub(crate) async fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
        match self {
            Self::BinanceSpot { source, .. } => source.reconnect_channel().await,
            Self::BinanceFutures { source, .. } => source.reconnect_channel().await,
            Self::BinanceOptions { source, .. } => source.reconnect_channel().await,
            Self::BinanceMargin { source, .. } => source.reconnect_channel().await,
            Self::Ibkr { source, .. } => source.reconnect_channel().await,
            Self::OkxTrading { source, .. } => source.reconnect_channel().await,
        }
    }

    pub(crate) async fn next_account_event(
        &mut self,
    ) -> Result<kairos_integration::application::ExternalAccountEventEnvelope, IntegrationError>
    {
        match self {
            Self::BinanceSpot { source, .. } => source.next_account_event().await,
            Self::BinanceFutures { source, .. } => source.next_account_event().await,
            Self::BinanceOptions { source, .. } => source.next_account_event().await,
            Self::BinanceMargin { source, .. } => source.next_account_event().await,
            Self::Ibkr { source, .. } => source.next_account_event().await,
            Self::OkxTrading { source, .. } => source.next_account_event().await,
        }
    }
}
use kairos_integration::application::{
    ExternalAccountEvent, ExternalAccountModel, ExternalAccountSegment, ExternalAccountStatus,
    ExternalBalance, ExternalDecimal, ExternalMarginMode, ExternalOrderStatus,
    ExternalPositionMode,
};

pub(crate) enum AccountSnapshotGateway {
    Memory(BTreeMap<String, AccountSnapshot>),
    Integration {
        connections: BTreeMap<String, Box<dyn AccountReadConnection + Send>>,
        resolver: AccountInstrumentResolver,
    },
}

impl AccountSnapshotGateway {
    pub(crate) fn memory(snapshots: BTreeMap<String, AccountSnapshot>) -> Self {
        Self::Memory(snapshots)
    }

    pub(crate) fn integration(
        connections: BTreeMap<String, Box<dyn AccountReadConnection + Send>>,
        resolver: AccountInstrumentResolver,
    ) -> Self {
        Self::Integration {
            connections,
            resolver,
        }
    }

    pub(crate) fn split(self) -> BTreeMap<String, Self> {
        match self {
            Self::Memory(snapshots) => snapshots
                .into_iter()
                .map(|(key, snapshot)| {
                    (key.clone(), Self::Memory(BTreeMap::from([(key, snapshot)])))
                })
                .collect(),
            Self::Integration {
                connections,
                resolver,
            } => connections
                .into_iter()
                .map(|(key, connection)| {
                    (
                        key.clone(),
                        Self::Integration {
                            connections: BTreeMap::from([(key, connection)]),
                            resolver: resolver.clone(),
                        },
                    )
                })
                .collect(),
        }
    }

    pub(crate) fn fetch(&mut self, segment: &AccountSegment) -> Result<AccountSnapshot, String> {
        match self {
            Self::Memory(snapshots) => snapshots
                .get(segment.segment_key.as_str())
                .cloned()
                .ok_or_else(|| format!("missing snapshot for segment: {}", segment.segment_key)),
            Self::Integration {
                connections,
                resolver,
            } => {
                let connection = connections
                    .get_mut(segment.segment_key.as_str())
                    .ok_or_else(|| {
                        format!("account segment is not configured: {}", segment.segment_key)
                    })?;
                connection
                    .fetch_account(&external_segment(segment))
                    .map_err(|error| error.to_string())
                    .and_then(|value| map_snapshot(value, resolver))
            }
        }
    }
}

pub(crate) struct AccountMarketProfileGateway {
    connections: BTreeMap<String, Box<dyn AccountMarketProfileConnection + Send>>,
}

impl AccountMarketProfileGateway {
    pub(crate) fn new(
        connections: BTreeMap<String, Box<dyn AccountMarketProfileConnection + Send>>,
    ) -> Self {
        Self { connections }
    }

    pub(crate) fn fetch(
        &mut self,
        request: &AccountMarketProfileRequest,
    ) -> Result<AccountMarketProfile, String> {
        let connection = self
            .connections
            .get_mut(request.segment_key.as_str())
            .ok_or_else(|| format!("account segment is not configured: {}", request.segment_key))?;
        connection
            .fetch_market_profile(&ExternalMarketProfileRequest {
                account_id: request.account_id.clone(),
                segment_key: kairos_domain_types::SegmentKey::new(request.segment_key.to_string())
                    .map_err(|error| error.to_string())?,
                market_id: request.market_id.clone(),
                source_symbol: request.source_symbol.clone(),
                market_data_access_id: request.market_data_access_id.clone(),
            })
            .map_err(|error| error.to_string())
            .and_then(map_profile)
    }
}

fn external_segment(segment: &AccountSegment) -> ExternalAccountSegment {
    ExternalAccountSegment {
        identity:
            kairos_integration::application::capabilities::account_facts::ExternalAccountIdentity {
                broker: segment.identity.broker.clone(),
                account_id: segment.identity.account_id.clone(),
            },
        segment_key: kairos_domain_types::SegmentKey::new(segment.segment_key.to_string())
            .expect("validated account segment key"),
        environment: segment.environment.clone(),
        account_model: segment.account_model.clone(),
    }
}

fn signed_quantity(value: ExternalDecimal) -> Result<SignedQuantity, String> {
    SignedQuantity::new(value.mantissa, value.scale).map_err(Into::into)
}

fn quantity(value: ExternalDecimal) -> Result<kairos_domain_types::Quantity, String> {
    kairos_domain_types::Quantity::new(value.mantissa, value.scale)
        .map_err(|error| error.to_string())
}

fn price(value: ExternalDecimal) -> Result<kairos_domain_types::Price, String> {
    kairos_domain_types::Price::new(value.mantissa, value.scale).map_err(|error| error.to_string())
}

fn money(value: ExternalDecimal) -> Result<Money, String> {
    Money::new(value.mantissa, value.scale).map_err(Into::into)
}

fn rate(value: ExternalDecimal) -> Result<kairos_domain_types::Rate, String> {
    kairos_domain_types::Rate::new(value.mantissa, value.scale).map_err(Into::into)
}

fn map_balance(value: ExternalBalance) -> Result<Balance, String> {
    Ok(Balance {
        asset_id: AssetId::new(value.asset_id.to_string()).expect("validated asset id"),
        asset_code: value.asset_code,
        total: signed_quantity(value.total)?,
        available: value.available.map(signed_quantity).transpose()?,
        locked: value.locked.map(signed_quantity).transpose()?,
        borrowed: value.borrowed.map(signed_quantity).transpose()?,
        interest: value.interest.map(signed_quantity).transpose()?,
    })
}

fn map_position(
    value: kairos_integration::application::ExternalPosition,
    resolver: &AccountInstrumentResolver,
) -> Result<Position, String> {
    let (instrument_id, market_id) = resolver.resolve(&value.provider_instrument)?;
    Ok(Position {
        instrument_id,
        market_id,
        quantity: signed_quantity(value.quantity)?,
        average_price: value.average_price.map(price).transpose()?,
        mark_price: value.mark_price.map(price).transpose()?,
        unrealized_pnl: value.unrealized_pnl.map(money).transpose()?,
        realized_pnl: value.realized_pnl.map(money).transpose()?,
        updated_at_unix_nanos: value.updated_at_unix_nanos,
    })
}

fn map_snapshot(
    value: kairos_integration::application::ExternalAccountSnapshot,
    resolver: &AccountInstrumentResolver,
) -> Result<AccountSnapshot, String> {
    Ok(AccountSnapshot {
        segment_key: SegmentKey::new(value.segment_key.to_string())
            .expect("validated account segment key"),
        balances: value
            .balances
            .into_iter()
            .map(map_balance)
            .collect::<Result<_, _>>()?,
        collateral: value
            .collateral
            .into_iter()
            .map(map_balance)
            .collect::<Result<_, _>>()?,
        positions: value
            .positions
            .into_iter()
            .map(|value| map_position(value, resolver))
            .collect::<Result<_, _>>()?,
        open_orders: value
            .open_orders
            .into_iter()
            .map(|value| map_open_order(value, resolver))
            .collect::<Result<_, _>>()?,
        status: map_status(value.status),
        observed_at_unix_nanos: value.observed_at_unix_nanos,
        equity: value.equity.map(money).transpose()?,
        initial_equity: value.initial_equity.map(money).transpose()?,
        net_profit: value.net_profit.map(money).transpose()?,
        account_model: value.account_model.map(map_model),
        margin_mode: value.margin_mode.map(map_margin),
        position_mode: value.position_mode.map(map_position_mode),
        kind: if value.partial {
            crate::domain::SnapshotKind::Delta
        } else {
            crate::domain::SnapshotKind::Full
        },
    })
}

fn map_open_order(
    value: kairos_integration::application::ExternalOpenOrder,
    resolver: &AccountInstrumentResolver,
) -> Result<OpenOrder, String> {
    let (instrument_id, _) = resolver.resolve(&value.provider_instrument)?;
    Ok(OpenOrder {
        order_id: value.order_id,
        remote_order_id: value.remote_order_id,
        instrument_id,
        side: value.side,
        quantity: quantity(value.quantity)?,
        filled_quantity: quantity(value.filled_quantity)?,
        status: value.status,
    })
}

fn map_status(value: ExternalAccountStatus) -> AccountStatus {
    match value {
        ExternalAccountStatus::Unknown => AccountStatus::Unknown,
        ExternalAccountStatus::Ready => AccountStatus::Ready,
        ExternalAccountStatus::Reconciling => AccountStatus::Reconciling,
        ExternalAccountStatus::TypeMismatch => AccountStatus::TypeMismatch,
        ExternalAccountStatus::Suspended => AccountStatus::Suspended,
        ExternalAccountStatus::Unavailable => AccountStatus::Unavailable,
    }
}

fn map_model(value: ExternalAccountModel) -> AccountModel {
    match value {
        ExternalAccountModel::NoMargin => AccountModel::NoMargin,
        ExternalAccountModel::Margin => AccountModel::Margin,
        ExternalAccountModel::Contract => AccountModel::Contract,
        ExternalAccountModel::ContractUnified => AccountModel::ContractUnified,
        ExternalAccountModel::Unified => AccountModel::Unified,
        ExternalAccountModel::PortfolioMargin => AccountModel::PortfolioMargin,
    }
}

fn map_margin(value: ExternalMarginMode) -> MarginMode {
    match value {
        ExternalMarginMode::Cross => MarginMode::Cross,
        ExternalMarginMode::Isolated => MarginMode::Isolated,
    }
}

fn map_position_mode(value: ExternalPositionMode) -> PositionMode {
    match value {
        ExternalPositionMode::OneWay => PositionMode::OneWay,
        ExternalPositionMode::Hedge => PositionMode::Hedge,
    }
}

fn map_order_status(value: ExternalOrderStatus) -> (&'static str, bool) {
    match value {
        ExternalOrderStatus::Acknowledged => ("acknowledged", true),
        ExternalOrderStatus::PartiallyFilled => ("partially_filled", true),
        ExternalOrderStatus::Filled => ("filled", false),
        ExternalOrderStatus::Canceled => ("canceled", false),
        ExternalOrderStatus::Rejected => ("rejected", false),
        ExternalOrderStatus::Expired => ("expired", false),
        ExternalOrderStatus::Unknown => ("unknown", true),
    }
}

pub(crate) fn map_event(
    value: ExternalAccountEvent,
    resolver: &AccountInstrumentResolver,
) -> Result<AccountEvent, String> {
    Ok(match value {
        ExternalAccountEvent::Batch(values) => AccountEvent::Batch(
            values
                .into_iter()
                .map(|value| map_event(value, resolver))
                .collect::<Result<_, _>>()?,
        ),
        ExternalAccountEvent::Snapshot(value) => {
            AccountEvent::Snapshot(map_snapshot(value, resolver)?)
        }
        ExternalAccountEvent::Order(value) => {
            let (status, active) = map_order_status(value.status);
            AccountEvent::OrderObserved(AccountOrderObservation {
                order_id: value.order_id,
                status: match status {
                    "acknowledged" => kairos_domain_types::OrderStatus::Acknowledged,
                    "partially_filled" => kairos_domain_types::OrderStatus::PartiallyFilled,
                    "filled" => kairos_domain_types::OrderStatus::Filled,
                    "canceled" => kairos_domain_types::OrderStatus::Canceled,
                    "rejected" => kairos_domain_types::OrderStatus::Rejected,
                    "expired" => kairos_domain_types::OrderStatus::Expired,
                    _ => kairos_domain_types::OrderStatus::Unknown,
                },
                active,
                remote_order_id: value.remote_order_id,
                filled_quantity: value.filled_quantity.map(quantity).transpose()?,
                observed_at_unix_nanos: value.occurred_at_unix_nanos,
            })
        }
        ExternalAccountEvent::Fill(value) => {
            let (instrument_id, _) = resolver.resolve(&value.provider_instrument)?;
            AccountEvent::ObservedFill(AccountObservedFill {
                fill_id: FillId::new(value.fill_id.to_string()).expect("validated fill id"),
                order_id: Some(value.order_id),
                remote_order_id: None,
                segment_key: SegmentKey::new(value.segment_key.to_string())
                    .expect("validated account segment key"),
                instrument_id,
                quantity: quantity(value.quantity)?,
                price: price(value.price)?,
                side: if value.side.eq_ignore_ascii_case("sell") {
                    crate::domain::FillSide::Sell
                } else {
                    crate::domain::FillSide::Buy
                },
                occurred_at_unix_nanos: value.occurred_at_unix_nanos,
            })
        }
    })
}

fn map_profile(
    value: kairos_integration::application::ExternalMarketProfile,
) -> Result<AccountMarketProfile, String> {
    Ok(AccountMarketProfile {
        account_id: value.account_id,
        segment_key: SegmentKey::new(value.segment_key.to_string())
            .map_err(|error| error.to_string())?,
        market_id: value.market_id,
        account_model: value.account_model.map(map_model),
        margin_mode: value.margin_mode.as_deref().and_then(|mode| match mode {
            "cross" => Some(MarginMode::Cross),
            "isolated" => Some(MarginMode::Isolated),
            _ => None,
        }),
        position_mode: value.position_mode.as_deref().and_then(|mode| match mode {
            "one_way" => Some(PositionMode::OneWay),
            "hedge" => Some(PositionMode::Hedge),
            _ => None,
        }),
        maker_fee: value.maker_fee.map(rate).transpose()?,
        taker_fee: value.taker_fee.map(rate).transpose()?,
        fee_currency: value.fee_currency,
        fee_discount: value.fee_discount.map(rate).transpose()?,
        fee_tier: value.fee_tier,
        source: value.source,
        observed_at_unix_nanos: value.observed_at_unix_nanos,
    })
}

#[cfg(test)]
mod identity_tests {
    use super::AccountInstrumentResolver;
    use kairos_integration::application::{
        ParticipantInstrumentTypeRef, ParticipantKind, ParticipantRef, ProviderInstrumentRef,
    };

    #[test]
    fn resolves_exchange_symbol_only_through_reference_market() {
        let resolver = AccountInstrumentResolver::fixture(
            vec![kairos_reference_contract::ReferenceMarket {
                source_id: Some("binance-spot".into()),
                market_id: "market:binance:spot:BTCUSDT".into(),
                market_key: "BTCUSDT".into(),
                instrument_id: "instrument:spot:BTC".into(),
                listing_id: "listing:binance:spot:BTCUSDT".into(),
                exchange_id: "exchange:binance".into(),
                market_type: "spot".into(),
                source_symbol: "BTCUSDT".into(),
                market_data_access_id: None,
                provider_symbol: None,
                status: "active".into(),
                asset_type: None,
                base_asset_id: None,
                quote_asset_id: None,
                underlying_instrument_id: None,
                price_tick: None,
                quantity_tick: None,
                minimum_quantity: None,
                minimum_notional: None,
                price_precision: 0,
                quantity_precision: 0,
                contract_size: None,
                effective_from_unix_nanos: 0,
                effective_to_unix_nanos: None,
            }],
            Vec::new(),
        );
        let provider = ProviderInstrumentRef::new(
            ParticipantRef::new(ParticipantKind::Exchange, "binance").unwrap(),
            Some(ParticipantInstrumentTypeRef::new("binance-spot").unwrap()),
            "BTCUSDT",
        )
        .unwrap();

        let (instrument, market) = resolver.resolve(&provider).unwrap();
        assert_eq!(instrument.as_str(), "instrument:spot:BTC");
        assert_eq!(
            market.as_ref().map(kairos_domain_types::MarketId::as_str),
            Some("market:binance:spot:BTCUSDT")
        );
    }

    #[test]
    fn resolves_ibkr_equity_to_reference_instrument_without_fabricating_market() {
        let resolver = AccountInstrumentResolver::fixture(
            Vec::new(),
            vec![kairos_reference_contract::model::Instrument {
                instrument_id: "instrument:equity:US:AAPL:common".into(),
                symbol: "AAPL".into(),
                instrument_type: "equity".into(),
                status: "active".into(),
                ..Default::default()
            }],
        );
        let provider = ProviderInstrumentRef::new(
            ParticipantRef::new(ParticipantKind::Broker, "ibkr").unwrap(),
            Some(ParticipantInstrumentTypeRef::new("equity").unwrap()),
            "AAPL",
        )
        .unwrap();

        let (instrument, market) = resolver.resolve(&provider).unwrap();
        assert_eq!(instrument.as_str(), "instrument:equity:US:AAPL:common");
        assert!(market.is_none());
    }

    #[test]
    fn refuses_missing_or_ambiguous_reference_identity() {
        let resolver = AccountInstrumentResolver::default();
        let provider = ProviderInstrumentRef::new(
            ParticipantRef::new(ParticipantKind::Exchange, "binance").unwrap(),
            Some(ParticipantInstrumentTypeRef::new("binance-spot").unwrap()),
            "BTCUSDT",
        )
        .unwrap();

        assert!(resolver.resolve(&provider).is_err());
    }
}
