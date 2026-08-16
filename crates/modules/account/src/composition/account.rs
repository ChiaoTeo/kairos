use std::path::PathBuf;

use crate::application::{AccountApplication, AccountProcess, AccountSnapshotPublisher};
use crate::composition::empty_snapshot;
use crate::domain::{
    AccountSegment, AccountSnapshot, AssetId, Balance, ExternalAccountIdentity, SegmentKey,
    SignedQuantity,
};
use crate::services::integration::{
    AccountAsyncEventSource, AccountAsyncMarketProfileConnection, AccountAsyncMarketProfileGateway,
    AccountAsyncSnapshotConnection, AccountAsyncSnapshotGateway, AccountInstrumentResolver,
    AccountMarketProfileGateway, AccountSnapshotGateway,
};
use crate::services::persistence::JsonAccountStore;
use kairos_integration::application::{
    AsyncAccountCredentialInspectionConnection, ExternalAccountCredentialProfile,
};
use kairos_integration::blocking::{AccountMarketProfileConnection, AccountReadConnection};
use kairos_integration::participants::binance::ConnectionDomain as BinanceConnectionDomain;
use kairos_integration::participants::binance::{
    BinanceCoinMConnection, BinanceFuturesChannelConfig, BinanceFuturesConnectionConfig,
    BinanceMarginChannelConfig, BinanceOptionsChannelConfig, BinanceOptionsConnection,
    BinanceOptionsConnectionConfig, BinancePrincipalConfig, BinancePrincipalOrderQuotaAllocation,
    BinanceQuotaAllocation, BinanceSharedQuotaConfig, BinanceSpotChannelConfig,
    BinanceSpotConnection, BinanceSpotConnectionConfig, BinanceSpotPrincipalConnection,
    BinanceUsdMConnection,
};
use kairos_integration::participants::okx::{
    InstrumentType as OkxInstrumentType, OkxConnection, OkxConnectionConfig, OkxPrincipalConfig,
    OkxPrincipalConnection, OkxPrincipalQuotaAllocation, OkxPrivateChannelConfig,
    OkxSharedQuotaConfig,
};
use kairos_integration::participants::{binance, ibkr};
use secrecy::{ExposeSecret, SecretString};

#[derive(Clone, Debug)]
pub struct AccountOptions {
    pub provider: String,
    pub product: String,
    pub api_key: SecretString,
    pub secret: SecretString,
    pub passphrase: SecretString,
    pub base_url: String,
    pub account_id: String,
    pub segment: String,
    pub environment: String,
    pub account_model: Option<String>,
    pub initial_balances: Vec<String>,
    pub host: String,
    pub port: u16,
    pub client_id: i32,
    /// Provider-native symbol owned by this Account binding for Binance
    /// isolated margin. It is never inferred from a canonical Market ID.
    pub isolated_margin_symbol: Option<String>,
    /// Workspace Reference SQLite database used read-only to resolve provider
    /// symbols into canonical business identity.
    pub reference_database: Option<PathBuf>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AccountSegmentBinding {
    pub segment_key: String,
    pub provider_product: String,
    pub trading_mode: Option<String>,
}

impl AccountSegmentBinding {
    pub fn new(segment_key: impl Into<String>, provider_product: impl Into<String>) -> Self {
        Self {
            segment_key: segment_key.into(),
            provider_product: provider_product.into(),
            trading_mode: None,
        }
    }

    pub fn with_trading_mode(mut self, trading_mode: impl Into<String>) -> Self {
        self.trading_mode = Some(trading_mode.into());
        self
    }
}

pub struct AccountComposition {
    pub application: AccountApplication,
    pub provider: String,
    async_account_streams: Vec<AccountAsyncEventSource>,
    instrument_resolver: AccountInstrumentResolver,
}

impl AccountComposition {
    pub fn into_process(
        self,
        account_id: impl Into<String>,
        socket_path: impl Into<PathBuf>,
        refresh_interval: std::time::Duration,
        health_file: Option<PathBuf>,
        publisher: Option<Box<dyn AccountSnapshotPublisher>>,
    ) -> Result<AccountProcess, String> {
        AccountProcess::new(
            self.application,
            account_id,
            socket_path,
            refresh_interval,
            health_file,
            publisher,
        )
        .map(|process| {
            process
                .with_async_account_streams(self.async_account_streams)
                .with_instrument_resolver(self.instrument_resolver)
        })
    }
}

fn connect_binance_principal(
    options: &AccountOptions,
    binding_id: impl Into<String>,
    shared_quota_ledger_path: Option<PathBuf>,
    egress_scope_id: &str,
) -> Result<BinanceSpotPrincipalConnection, String> {
    let provider = BinanceSpotConnection::connect(BinanceSpotConnectionConfig {
        environment: options.environment.clone(),
        rest_base_url: options.base_url.clone(),
        quota: BinanceQuotaAllocation {
            request_weight_per_minute: 1_000,
            cancel_reserve_weight: 50,
        },
        shared_quota: shared_quota_ledger_path.map(|ledger_path| BinanceSharedQuotaConfig {
            ledger_path,
            egress_scope_id: egress_scope_id.to_owned(),
        }),
    })
    .map_err(|error| error.to_string())?;
    provider
        .principal_connection(BinancePrincipalConfig {
            binding_id: binding_id.into(),
            principal_id: (!options.account_id.trim().is_empty())
                .then(|| options.account_id.clone()),
            api_key: options.api_key.clone(),
            secret: options.secret.clone(),
            principal_quota: None::<BinancePrincipalOrderQuotaAllocation>,
        })
        .map_err(|error| error.to_string())
}

fn connect_binance_futures_principal(
    options: &AccountOptions,
    binding_id: impl Into<String>,
    coin_m: bool,
) -> Result<kairos_integration::participants::binance::BinanceFuturesPrincipalConnection, String> {
    let config = BinanceFuturesConnectionConfig {
        environment: options.environment.clone(),
        rest_base_url: options.base_url.clone(),
        quota: BinanceQuotaAllocation {
            request_weight_per_minute: 1_000,
            cancel_reserve_weight: 50,
        },
        shared_quota: None,
    };
    let provider = if coin_m {
        BinanceCoinMConnection::connect(config)
            .map_err(|error| error.to_string())?
            .principal_connection(BinancePrincipalConfig {
                binding_id: binding_id.into(),
                principal_id: (!options.account_id.trim().is_empty())
                    .then(|| options.account_id.clone()),
                api_key: options.api_key.clone(),
                secret: options.secret.clone(),
                principal_quota: None,
            })
    } else {
        BinanceUsdMConnection::connect(config)
            .map_err(|error| error.to_string())?
            .principal_connection(BinancePrincipalConfig {
                binding_id: binding_id.into(),
                principal_id: (!options.account_id.trim().is_empty())
                    .then(|| options.account_id.clone()),
                api_key: options.api_key.clone(),
                secret: options.secret.clone(),
                principal_quota: None,
            })
    };
    provider.map_err(|error| error.to_string())
}

fn connect_binance_options_principal(
    options: &AccountOptions,
    binding_id: impl Into<String>,
) -> Result<kairos_integration::participants::binance::BinanceOptionsPrincipalConnection, String> {
    BinanceOptionsConnection::connect(BinanceOptionsConnectionConfig {
        environment: options.environment.clone(),
        rest_base_url: options.base_url.clone(),
        quota: BinanceQuotaAllocation {
            request_weight_per_minute: 1_000,
            cancel_reserve_weight: 50,
        },
        shared_quota: None,
    })
    .map_err(|error| error.to_string())?
    .principal_connection(BinancePrincipalConfig {
        binding_id: binding_id.into(),
        principal_id: (!options.account_id.trim().is_empty()).then(|| options.account_id.clone()),
        api_key: options.api_key.clone(),
        secret: options.secret.clone(),
        principal_quota: None,
    })
    .map_err(|error| error.to_string())
}

fn okx_instrument_type(segment: &str) -> Result<OkxInstrumentType, String> {
    match segment
        .trim()
        .to_ascii_lowercase()
        .replace('_', "-")
        .as_str()
    {
        "spot" => Ok(OkxInstrumentType::Spot),
        "margin" => Ok(OkxInstrumentType::Margin),
        "swap" => Ok(OkxInstrumentType::Swap),
        "futures" => Ok(OkxInstrumentType::Futures),
        "option" | "options" => Ok(OkxInstrumentType::Option),
        value => Err(format!("unsupported OKX account segment: {value}")),
    }
}

fn connect_okx_principal(
    options: &AccountOptions,
    binding_id: impl Into<String>,
    shared_quota_ledger_path: Option<PathBuf>,
    egress_scope_id: &str,
) -> Result<OkxPrincipalConnection, String> {
    let binding_id = binding_id.into();
    let quota_enabled = shared_quota_ledger_path.is_some();
    let provider = OkxConnection::connect(OkxConnectionConfig {
        environment: options.environment.clone(),
        rest_base_url: options.base_url.clone(),
        shared_quota: shared_quota_ledger_path.map(|ledger_path| OkxSharedQuotaConfig {
            ledger_path,
            egress_scope_id: egress_scope_id.to_owned(),
        }),
    })
    .map_err(|error| error.to_string())?;
    let principal_id = if options.account_id.trim().is_empty() {
        binding_id.clone()
    } else {
        options.account_id.clone()
    };
    provider
        .principal_connection(OkxPrincipalConfig {
            binding_id,
            principal_id: Some(principal_id),
            api_key: options.api_key.clone(),
            secret: options.secret.clone(),
            passphrase: options.passphrase.clone(),
            quota: quota_enabled.then_some(OkxPrincipalQuotaAllocation {
                private_requests_per_two_seconds: 10,
            }),
            order_quota: None,
        })
        .map_err(|error| error.to_string())
}

fn normalized_segment(segment: &str) -> String {
    segment.trim().to_ascii_lowercase().replace('_', "-")
}

/// Exact provider/product endpoint selection for Account composition. Unknown
/// combinations fail instead of inheriting another provider's endpoint.
pub fn default_rest_endpoint(provider: &str, product: &str) -> Result<&'static str, String> {
    let provider = normalized_provider(provider);
    let product = normalized_segment(product);
    match (provider.as_str(), product.as_str()) {
        ("binance", "spot" | "funding" | "cross-margin" | "isolated-margin") => {
            Ok("https://api.binance.com")
        }
        ("binance", "usd-m-futures") => Ok("https://fapi.binance.com"),
        ("binance", "coin-m-futures") => Ok("https://dapi.binance.com"),
        ("binance", "options") => Ok("https://eapi.binance.com"),
        ("okx", "spot" | "margin" | "swap" | "futures" | "option" | "options") => {
            Ok("https://www.okx.com")
        }
        ("ibkr", "equity" | "stocks" | "spot") | ("paper" | "simulated", _) => Ok(""),
        _ => Err(format!(
            "unsupported Account provider/product: {provider}/{product}"
        )),
    }
}

fn binance_endpoint_family(segment: &str) -> Result<&'static str, String> {
    match normalized_segment(segment).as_str() {
        "spot" | "funding" | "cross-margin" | "isolated-margin" => Ok("spot"),
        "usd-m-futures" => Ok("usd-m-futures"),
        "coin-m-futures" => Ok("coin-m-futures"),
        "options" => Ok("options"),
        value => Err(format!(
            "unsupported Binance async Account segment: {value}"
        )),
    }
}

fn binance_rest_base_url(options: &AccountOptions, family: &str) -> String {
    if options.base_url.trim_end_matches('/') != "https://api.binance.com" {
        return options.base_url.clone();
    }
    match family {
        "usd-m-futures" => "https://fapi.binance.com".into(),
        "coin-m-futures" => "https://dapi.binance.com".into(),
        "options" => "https://eapi.binance.com".into(),
        _ => options.base_url.clone(),
    }
}

/// Compose one Binance REST endpoint family from one principal connection.
/// All readers share signer, clock, HTTP scheduling and quota. Spot projects
/// a private event channel; the remaining products currently use async
/// snapshot recovery while their Account private-stream projections migrate.
pub fn compose_binance_async_account_application(
    options: &AccountOptions,
    segments: &[AccountSegmentBinding],
    state: Option<PathBuf>,
    websocket_api_url: &str,
    shared_quota_ledger_path: Option<PathBuf>,
    egress_scope_id: &str,
) -> Result<AccountComposition, String> {
    if normalized_provider(&options.provider) != "binance" {
        return Err("Binance async composition requires provider=binance".into());
    }
    if segments.is_empty() {
        return Err("Binance async composition requires at least one segment".into());
    }
    let families = segments
        .iter()
        .map(|segment| binance_endpoint_family(&segment.provider_product))
        .collect::<Result<std::collections::BTreeSet<_>, _>>()?;
    if families.len() != 1 {
        return Err(format!(
            "Binance async Account segments must share one REST endpoint family; got {}",
            families.into_iter().collect::<Vec<_>>().join(",")
        ));
    }
    let family = binance_endpoint_family(&segments[0].provider_product)?;
    let futures_stream_endpoint = match family {
        "usd-m-futures" => "wss://fstream.binance.com",
        "coin-m-futures" => "wss://dstream.binance.com",
        _ => websocket_api_url,
    };
    let mut connection_options = options.clone();
    connection_options.base_url = binance_rest_base_url(options, family);
    let private = connect_binance_principal(
        &connection_options,
        "account.binance",
        shared_quota_ledger_path,
        egress_scope_id,
    )?;
    let identity = ExternalAccountIdentity::new("binance", options.account_id.clone())
        .map_err(|error| error.to_string())?;
    let mut account_segments = Vec::with_capacity(segments.len());
    let mut sources = std::collections::BTreeMap::new();
    let mut profile_sources = std::collections::BTreeMap::new();
    let mut streams = Vec::new();
    for configured_segment in segments {
        let segment_key = configured_segment.segment_key.clone();
        let provider_product = normalized_segment(&configured_segment.provider_product);
        let account_model = match binance_endpoint_family(&provider_product)? {
            "spot" if provider_product == "spot" || provider_product == "funding" => "no_margin",
            "spot" => "margin",
            _ => "contract",
        };
        account_segments.push(AccountSegment {
            identity: identity.clone(),
            segment_key: SegmentKey::new(segment_key.clone()).map_err(|error| error.to_string())?,
            environment: options.environment.clone(),
            account_model: Some(
                options
                    .account_model
                    .clone()
                    .unwrap_or_else(|| account_model.into()),
            ),
        });
        let read = match provider_product.as_str() {
            "spot" => {
                let stream = private
                    .spot_account_events(
                        segment_key.clone(),
                        &BinanceSpotChannelConfig {
                            websocket_api_url: websocket_api_url.to_owned(),
                            event_queue_capacity: 256,
                        },
                    )
                    .map(|source| AccountAsyncEventSource::BinanceSpot {
                        binding_id: format!("account.binance.spot.{segment_key}"),
                        source,
                    })
                    .map_err(|error| error.to_string())?;
                streams.push(stream);
                profile_sources.insert(
                    segment_key.clone(),
                    AccountAsyncMarketProfileConnection::BinanceSpot(
                        private.spot_account_market_profile(),
                    ),
                );
                AccountAsyncSnapshotConnection::BinanceSpot(private.spot_account_read())
            }
            "funding" => {
                AccountAsyncSnapshotConnection::BinanceFunding(private.funding_account_read())
            }
            "cross-margin" => {
                let connection = private.cross_margin_connection();
                streams.push(AccountAsyncEventSource::BinanceMargin {
                    binding_id: format!("account.binance.cross-margin.{segment_key}"),
                    source: connection
                        .account_events(
                            segment_key.clone(),
                            &BinanceMarginChannelConfig {
                                websocket_stream_url: "wss://stream.binance.com:9443".into(),
                                isolated_symbol: None,
                                event_queue_capacity: 256,
                            },
                        )
                        .map_err(|error| error.to_string())?,
                });
                AccountAsyncSnapshotConnection::BinanceMargin(connection.account_read())
            }
            "isolated-margin" => {
                let provider_symbol =
                    options.isolated_margin_symbol.as_deref().ok_or_else(|| {
                        "Binance isolated-margin Account requires values.isolated_margin_symbol"
                            .to_string()
                    })?;
                let connection = private
                    .isolated_margin_connection(provider_symbol)
                    .map_err(|error| error.to_string())?;
                streams.push(AccountAsyncEventSource::BinanceMargin {
                    binding_id: format!("account.binance.isolated-margin.{segment_key}"),
                    source: connection
                        .account_events(
                            segment_key.clone(),
                            &BinanceMarginChannelConfig {
                                websocket_stream_url: "wss://stream.binance.com:9443".into(),
                                isolated_symbol: Some(provider_symbol.to_owned()),
                                event_queue_capacity: 256,
                            },
                        )
                        .map_err(|error| error.to_string())?,
                });
                AccountAsyncSnapshotConnection::BinanceMargin(connection.account_read())
            }
            "usd-m-futures" => {
                let connection = connect_binance_futures_principal(
                    options,
                    format!("account.binance.usd-m-futures.{segment_key}"),
                    false,
                )?;
                streams.push(AccountAsyncEventSource::BinanceFutures {
                    binding_id: format!("account.binance.usd-m-futures.{segment_key}"),
                    source: connection
                        .account_events(
                            segment_key.clone(),
                            &BinanceFuturesChannelConfig {
                                websocket_stream_url: futures_stream_endpoint.to_owned(),
                                event_queue_capacity: 256,
                            },
                        )
                        .map_err(|error| error.to_string())?,
                });
                AccountAsyncSnapshotConnection::BinanceFutures(connection.account_read())
            }
            "coin-m-futures" => {
                let connection = connect_binance_futures_principal(
                    options,
                    format!("account.binance.coin-m-futures.{segment_key}"),
                    true,
                )?;
                streams.push(AccountAsyncEventSource::BinanceFutures {
                    binding_id: format!("account.binance.coin-m-futures.{segment_key}"),
                    source: connection
                        .account_events(
                            segment_key.clone(),
                            &BinanceFuturesChannelConfig {
                                websocket_stream_url: futures_stream_endpoint.to_owned(),
                                event_queue_capacity: 256,
                            },
                        )
                        .map_err(|error| error.to_string())?,
                });
                AccountAsyncSnapshotConnection::BinanceFutures(connection.account_read())
            }
            "options" => {
                let connection = connect_binance_options_principal(
                    options,
                    format!("account.binance.options.{segment_key}"),
                )?;
                streams.push(AccountAsyncEventSource::BinanceOptions {
                    binding_id: format!("account.binance.options.{segment_key}"),
                    source: connection
                        .account_events(
                            segment_key.clone(),
                            &BinanceOptionsChannelConfig {
                                websocket_stream_url:
                                    "wss://nbstream.binance.com/eoptions/private/stream".into(),
                                event_queue_capacity: 256,
                            },
                        )
                        .map_err(|error| error.to_string())?,
                });
                AccountAsyncSnapshotConnection::BinanceOptions(connection.account_read())
            }
            _ => unreachable!("validated Binance Account segment"),
        };
        sources.insert(segment_key, read);
    }
    let instrument_resolver = load_instrument_resolver(options)?;
    let mut application = AccountApplication::with_async_dependencies(
        account_segments,
        state.map(JsonAccountStore::new),
    )
    .map_err(|error| error.to_string())?;
    application.attach_async_sources(
        AccountAsyncSnapshotGateway::new(sources, instrument_resolver.clone()),
        (!profile_sources.is_empty())
            .then(|| AccountAsyncMarketProfileGateway::new(profile_sources)),
    );
    Ok(AccountComposition {
        application,
        provider: "binance".into(),
        async_account_streams: streams,
        instrument_resolver,
    })
}

/// Compose all configured OKX trading-account segments from one provider and
/// principal context. `InstrumentType` remains an OKX request filter; every
/// projected capability belongs to `okx::ConnectionDomain::Trading`.
pub fn compose_okx_async_account_application(
    options: &AccountOptions,
    segments: &[AccountSegmentBinding],
    state: Option<PathBuf>,
    private_websocket_url: Option<&str>,
    shared_quota_ledger_path: Option<PathBuf>,
    egress_scope_id: &str,
) -> Result<AccountComposition, String> {
    if normalized_provider(&options.provider) != "okx" {
        return Err("OKX async composition requires provider=okx".into());
    }
    if segments.is_empty() {
        return Err("OKX async composition requires at least one segment".into());
    }
    for segment in segments {
        let product = normalized_segment(&segment.provider_product);
        let mode = segment.trading_mode.as_deref().map(normalized_segment);
        match (product.as_str(), mode.as_deref()) {
            ("spot", None | Some("cash")) => {}
            ("margin" | "swap" | "futures" | "option" | "options", Some("cross" | "isolated")) => {}
            ("spot", Some(_)) => return Err("OKX spot Account requires cash trading_mode".into()),
            ("margin" | "swap" | "futures" | "option" | "options", None) => {
                return Err(format!(
                "OKX {product} Account segment requires explicit trading_mode (cross or isolated)"
            ))
            }
            (_, Some(value)) => return Err(format!("unsupported OKX trading mode: {value}")),
            _ => {}
        }
    }
    let instrument_types = segments
        .iter()
        .map(|segment| okx_instrument_type(&segment.provider_product))
        .collect::<Result<Vec<_>, _>>()?;
    let principal = connect_okx_principal(
        options,
        "account.okx",
        shared_quota_ledger_path,
        egress_scope_id,
    )?;
    let identity = ExternalAccountIdentity::new("okx", options.account_id.clone())
        .map_err(|error| error.to_string())?;
    let mut account_segments = Vec::with_capacity(segments.len());
    let mut sources = std::collections::BTreeMap::new();
    let mut profile_sources = std::collections::BTreeMap::new();
    let mut streams = Vec::new();
    for (configured_segment, instrument_type) in segments.iter().zip(instrument_types) {
        let segment_key = configured_segment.segment_key.clone();
        account_segments.push(AccountSegment {
            identity: identity.clone(),
            segment_key: SegmentKey::new(segment_key.clone()).map_err(|error| error.to_string())?,
            environment: options.environment.clone(),
            account_model: options.account_model.clone(),
        });

        sources.insert(
            segment_key.clone(),
            AccountAsyncSnapshotConnection::OkxTrading(principal.trading_account(instrument_type)),
        );
        profile_sources.insert(
            segment_key.clone(),
            AccountAsyncMarketProfileConnection::OkxTrading(
                principal.trading_account_market_profile(instrument_type),
            ),
        );
        if let Some(websocket_url) = private_websocket_url {
            streams.push(AccountAsyncEventSource::OkxTrading {
                binding_id: format!("account.okx.trading.{segment_key}"),
                source: principal
                    .trading_account_events(
                        instrument_type,
                        segment_key,
                        &OkxPrivateChannelConfig {
                            websocket_url: websocket_url.to_owned(),
                            event_queue_capacity: 256,
                        },
                    )
                    .map_err(|error| error.to_string())?,
            });
        }
    }
    let instrument_resolver = load_instrument_resolver(options)?;
    let mut application = AccountApplication::with_async_dependencies(
        account_segments,
        state.map(JsonAccountStore::new),
    )
    .map_err(|error| error.to_string())?;
    application.attach_async_sources(
        AccountAsyncSnapshotGateway::new(sources, instrument_resolver.clone()),
        Some(AccountAsyncMarketProfileGateway::new(profile_sources)),
    );
    Ok(AccountComposition {
        application,
        provider: "okx".into(),
        async_account_streams: streams,
        instrument_resolver,
    })
}

/// Compose IBKR Account capabilities from the same native async hard session
/// used by execution for a TWS/Gateway client identity.
pub fn compose_ibkr_async_account_application(
    options: &AccountOptions,
    segments: &[AccountSegmentBinding],
    state: Option<PathBuf>,
) -> Result<AccountComposition, String> {
    if normalized_provider(&options.provider) != "ibkr" {
        return Err("IBKR async composition requires provider=ibkr".into());
    }
    if segments.is_empty()
        || segments.iter().any(|segment| {
            !matches!(
                normalized_segment(&segment.provider_product).as_str(),
                "equity" | "spot"
            )
        })
    {
        return Err("IBKR Account supports only an equity segment".into());
    }
    if segments.len() != 1 {
        return Err("IBKR Account requires exactly one equity segment per client session".into());
    }
    let segment_key = segments[0].segment_key.clone();
    let connection = ibkr::IbkrConnection::connect(
        ibkr::IbkrConnectionConfig {
            host: options.host.clone(),
            port: options.port,
            client_id: options.client_id,
        },
        "account.ibkr.equity",
        options.account_id.clone(),
    )
    .map_err(|error| error.to_string())?;
    let identity = ExternalAccountIdentity::new("ibkr", options.account_id.clone())
        .map_err(|error| error.to_string())?;
    let account_segments = vec![AccountSegment {
        identity,
        segment_key: SegmentKey::new(segment_key.clone()).map_err(|error| error.to_string())?,
        environment: options.environment.clone(),
        account_model: Some(
            options
                .account_model
                .clone()
                .unwrap_or_else(|| "no_margin".into()),
        ),
    }];
    let instrument_resolver = load_instrument_resolver(options)?;
    let mut application = AccountApplication::with_async_dependencies(
        account_segments,
        state.map(JsonAccountStore::new),
    )
    .map_err(|error| error.to_string())?;
    application.attach_async_sources(
        AccountAsyncSnapshotGateway::new(
            std::collections::BTreeMap::from([(
                segment_key.clone(),
                AccountAsyncSnapshotConnection::Ibkr(connection.account_read()),
            )]),
            instrument_resolver.clone(),
        ),
        None,
    );
    let source = connection
        .account_events(segment_key.clone())
        .map_err(|error| error.to_string())?;
    Ok(AccountComposition {
        application,
        provider: "ibkr".into(),
        async_account_streams: vec![AccountAsyncEventSource::Ibkr {
            binding_id: format!("account.ibkr.equity.{segment_key}"),
            source,
        }],
        instrument_resolver,
    })
}

/// Inspect an account credential using the participant capability selected by
/// Account composition. Integration exposes the capability; it does not own
/// this cross-participant business selection.
pub async fn inspect_account_credential(
    options: &AccountOptions,
    shared_quota_ledger_path: Option<PathBuf>,
    egress_scope_id: &str,
) -> Result<ExternalAccountCredentialProfile, String> {
    let provider = normalized_provider(&options.provider);
    let product = options
        .product
        .trim()
        .to_ascii_lowercase()
        .replace('_', "-");
    if provider == "binance" {
        let family = binance_endpoint_family(&product)?;
        let mut connection_options = options.clone();
        connection_options.base_url = binance_rest_base_url(options, family);
        let principal = connect_binance_principal(
            &connection_options,
            format!("account.binance.{product}.inspect"),
            shared_quota_ledger_path.clone(),
            egress_scope_id,
        )?;
        return match product.as_str() {
            "spot" => {
                principal
                    .spot_credential_inspection()
                    .inspect_credential()
                    .await
            }
            "funding" => {
                principal
                    .funding_credential_inspection()
                    .inspect_credential()
                    .await
            }
            "cross-margin" => {
                principal
                    .cross_margin_connection()
                    .credential_inspection()
                    .inspect_credential()
                    .await
            }
            "usd-m-futures" => {
                connect_binance_futures_principal(options, "account.binance.usdm.inspect", false)?
                    .credential_inspection()
                    .inspect_credential()
                    .await
            }
            "coin-m-futures" => {
                connect_binance_futures_principal(options, "account.binance.coinm.inspect", true)?
                    .credential_inspection()
                    .inspect_credential()
                    .await
            }
            "options" => {
                connect_binance_options_principal(options, "account.binance.options.inspect")?
                    .credential_inspection()
                    .inspect_credential()
                    .await
            }
            _ => unreachable!("validated Binance credential product"),
        }
        .map_err(|error| error.to_string());
    }
    if provider == "okx" {
        let instrument_type = okx_instrument_type(&product)?;
        let principal = connect_okx_principal(
            options,
            "account.okx.trading.inspect",
            shared_quota_ledger_path,
            egress_scope_id,
        )?;
        let mut inspection = principal.trading_credential_inspection(instrument_type);
        return inspection
            .inspect_credential()
            .await
            .map_err(|error| error.to_string());
    }

    // This is an explicit administration/CLI boundary for provider slices
    // which do not yet expose a participant-native async inspection handle.
    // The production Account process never enters this path.
    let options = options.clone();
    tokio::task::spawn_blocking(move || inspect_legacy_account_credential(&options))
        .await
        .map_err(|error| format!("credential inspection worker failed: {error}"))?
}

fn inspect_legacy_account_credential(
    options: &AccountOptions,
) -> Result<ExternalAccountCredentialProfile, String> {
    let mut connection = compose_blocking_credential_inspection(options)?;
    connection.inspect_credential()
}

pub fn compose_blocking_account_application(
    options: &AccountOptions,
    state: Option<PathBuf>,
) -> Result<AccountComposition, String> {
    compose_blocking_account_application_for_segments(
        options,
        &[AccountSegmentBinding::new(
            &options.segment,
            &options.product,
        )],
        state,
    )
}

pub fn compose_local_account_application_for_segments(
    options: &AccountOptions,
    segments: &[AccountSegmentBinding],
    state: Option<PathBuf>,
) -> Result<AccountComposition, String> {
    if !matches!(
        normalized_provider(&options.provider).as_str(),
        "paper" | "simulated"
    ) {
        return Err("local Account composition requires provider=paper or simulated".into());
    }
    compose_blocking_account_application_for_segments(options, segments, state)
}

/// Compose an explicit blocking/CLI account actor with every configured
/// segment for the account.
///
/// A provider connection remains the integration-owned source, while the
/// account actor owns the complete set of segment state.  Keeping this
/// This compatibility boundary is for administration, offline use, and
/// deterministic local sources. Production server composition selects only
/// provider-native async sources.
pub fn compose_blocking_account_application_for_segments(
    options: &AccountOptions,
    segments: &[AccountSegmentBinding],
    state: Option<PathBuf>,
) -> Result<AccountComposition, String> {
    if segments.is_empty() {
        return Err("at least one account segment is required".into());
    }
    let provider = normalized_provider(&options.provider);
    if provider == "paper" || provider == "simulated" {
        let identity = ExternalAccountIdentity::new(&provider, options.account_id.clone())
            .map_err(|error| error.to_string())?;
        let account_segments: Vec<_> = segments
            .iter()
            .map(|segment| AccountSegment {
                identity: identity.clone(),
                segment_key: SegmentKey::new(segment.segment_key.clone())
                    .expect("configured segment is required"),
                environment: options.environment.clone(),
                account_model: Some(
                    options
                        .account_model
                        .clone()
                        .or_else(|| {
                            options
                                .product
                                .eq_ignore_ascii_case("margin")
                                .then_some("margin".into())
                        })
                        .unwrap_or_else(|| "no_margin".into()),
                ),
            })
            .collect();
        let snapshots = segments
            .iter()
            .map(|segment| {
                let mut snapshot = empty_snapshot(segment.segment_key.clone());
                snapshot.balances = options
                    .initial_balances
                    .iter()
                    .map(|value| parse_initial_balance(value))
                    .collect::<Result<Vec<_>, _>>()?;
                Ok((segment.segment_key.clone(), snapshot))
            })
            .collect::<Result<std::collections::BTreeMap<_, _>, String>>()?;
        let application = AccountApplication::with_dependencies(
            account_segments,
            AccountSnapshotGateway::memory(snapshots),
            state.map(JsonAccountStore::new),
        )
        .map_err(|error| error.to_string())?;
        return Ok(AccountComposition {
            application,
            provider,
            async_account_streams: Vec::new(),
            instrument_resolver: AccountInstrumentResolver::default(),
        });
    }
    let identity = ExternalAccountIdentity::new(&provider, options.account_id.clone())
        .map_err(|error| error.to_string())?;
    let mut account_segments = Vec::with_capacity(segments.len());
    let mut sources = std::collections::BTreeMap::new();
    let mut profile_sources = std::collections::BTreeMap::new();
    for segment in segments {
        let segment_key = &segment.segment_key;
        let mut segment_options = options.clone();
        segment_options.product = segment.provider_product.clone();
        let product = account_product(&segment_options)?;
        let account_model = options.account_model.clone().unwrap_or_else(|| {
            if product == AccountProduct::Spot {
                "no_margin".into()
            } else if matches!(
                product,
                AccountProduct::CrossMargin | AccountProduct::IsolatedMargin
            ) {
                "margin".into()
            } else {
                "contract".into()
            }
        });
        account_segments.push(AccountSegment {
            identity: identity.clone(),
            segment_key: SegmentKey::new(segment_key.clone()).map_err(|error| error.to_string())?,
            environment: options.environment.clone(),
            account_model: Some(account_model),
        });
        let connection = compose_blocking_account(&segment_options)?;
        sources.insert(segment_key.clone(), connection);
        if let Some(connection) = compose_blocking_market_profile(&segment_options)? {
            profile_sources.insert(segment_key.clone(), connection);
        }
    }
    let instrument_resolver = load_instrument_resolver(options)?;
    let mut application = AccountApplication::with_dependencies(
        account_segments,
        AccountSnapshotGateway::integration(sources, instrument_resolver.clone()),
        state.map(JsonAccountStore::new),
    )
    .map_err(|error| error.to_string())?;
    if !profile_sources.is_empty() {
        application.attach_market_profile_source(AccountMarketProfileGateway::new(profile_sources));
    }
    Ok(AccountComposition {
        application,
        provider,
        async_account_streams: Vec::new(),
        instrument_resolver,
    })
}

/// Build an Account application around deterministic snapshots.
///
/// This is the supported fixture boundary for business tests and simulations;
/// callers do not need to implement Account's internal IO dependencies.
pub fn compose_in_memory_account_application(
    segments: Vec<AccountSegment>,
    snapshots: std::collections::BTreeMap<String, AccountSnapshot>,
    state: Option<PathBuf>,
) -> Result<AccountApplication, String> {
    AccountApplication::with_dependencies(
        segments,
        AccountSnapshotGateway::memory(snapshots),
        state.map(JsonAccountStore::new),
    )
    .map_err(|error| error.to_string())
}

fn parse_initial_balance(value: &str) -> Result<Balance, String> {
    let (asset, quantity) = value
        .split_once('=')
        .ok_or_else(|| format!("initial balance must be ASSET=QUANTITY: {value}"))?;
    let asset_code = asset.trim().to_ascii_uppercase();
    if asset_code.is_empty() {
        return Err("initial balance asset is required".into());
    }
    let quantity = quantity.trim();
    let total = quantity
        .parse::<SignedQuantity>()
        .map_err(|_| format!("invalid initial balance quantity: {quantity}"))?;
    Ok(Balance {
        asset_id: AssetId::new(format!("asset:{}", asset_code.to_ascii_lowercase()))
            .map_err(|error| error.to_string())?,
        asset_code: kairos_primitives::Currency::new(asset_code)
            .map_err(|error| error.to_string())?,
        total,
        available: None,
        locked: None,
        borrowed: None,
        interest: None,
    })
}

fn load_instrument_resolver(options: &AccountOptions) -> Result<AccountInstrumentResolver, String> {
    options
        .reference_database
        .as_ref()
        .map(AccountInstrumentResolver::from_reference_database)
        .transpose()
        .map(|value| value.unwrap_or_default())
}

pub fn normalized_provider(provider: &str) -> String {
    match provider.trim().to_ascii_lowercase().as_str() {
        "okex" => "okx".into(),
        value => value.into(),
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AccountProduct {
    Spot,
    CrossMargin,
    IsolatedMargin,
    UsdMFutures,
    CoinMFutures,
    Options,
    Equity,
}

pub fn account_product(options: &AccountOptions) -> Result<AccountProduct, String> {
    let provider = normalized_provider(&options.provider);
    let product = options
        .product
        .trim()
        .to_ascii_lowercase()
        .replace('_', "-");
    match provider.as_str() {
        "binance" => match product.as_str() {
            "spot" => Ok(AccountProduct::Spot),
            "cross-margin" => Ok(AccountProduct::CrossMargin),
            "isolated-margin" => Ok(AccountProduct::IsolatedMargin),
            "options" => Ok(AccountProduct::Options),
            "usd-m-futures" => Ok(AccountProduct::UsdMFutures),
            "coin-m-futures" => Ok(AccountProduct::CoinMFutures),
            _ => Err(format!("unsupported Binance account product: {product}")),
        },
        "okx" => Err(
            "OKX account REST uses compose_okx_async_account_application; the legacy registry path has been removed"
                .into(),
        ),
        "ibkr" => {
            if !matches!(product.as_str(), "spot" | "equity") {
                return Err(format!("unsupported IBKR account product: {product}"));
            }
            Ok(AccountProduct::Equity)
        }
        _ => Err(format!("unsupported account provider: {provider}")),
    }
}

fn binance_product(product: AccountProduct) -> Result<BinanceConnectionDomain, String> {
    match product {
        AccountProduct::Spot => Ok(BinanceConnectionDomain::Spot),
        AccountProduct::CrossMargin => Ok(BinanceConnectionDomain::CrossMargin),
        AccountProduct::IsolatedMargin => Ok(BinanceConnectionDomain::IsolatedMargin),
        AccountProduct::UsdMFutures => Ok(BinanceConnectionDomain::UsdMFutures),
        AccountProduct::CoinMFutures => Ok(BinanceConnectionDomain::CoinMFutures),
        AccountProduct::Options => Ok(BinanceConnectionDomain::Options),
        AccountProduct::Equity => Err("Binance account equity is not supported".into()),
    }
}

pub fn compose_blocking_account(
    options: &AccountOptions,
) -> Result<Box<dyn AccountReadConnection + Send>, String> {
    let provider = normalized_provider(&options.provider);
    let product = account_product(options)?;
    let key = options.api_key.expose_secret().to_owned();
    let secret = options.secret.expose_secret().to_owned();
    match provider.as_str() {
        "binance" => match product {
            AccountProduct::Spot => {
                binance::blocking::spot_account(key, secret, options.base_url.clone())
            }
            AccountProduct::CrossMargin | AccountProduct::IsolatedMargin => {
                binance::blocking::margin_account(
                    binance_product(product)?,
                    key,
                    secret,
                    options.base_url.clone(),
                )
            }
            AccountProduct::UsdMFutures | AccountProduct::CoinMFutures => {
                binance::blocking::futures_account(
                    binance_product(product)?,
                    key,
                    secret,
                    options.base_url.clone(),
                )
            }
            AccountProduct::Options => {
                binance::blocking::options_account(key, secret, options.base_url.clone())
            }
            AccountProduct::Equity => unreachable!(),
        }
        .map_err(|error| error.to_string()),
        "ibkr" => ibkr::blocking::account(&ibkr::IbkrConnectionConfig {
            host: options.host.clone(),
            port: options.port,
            client_id: options.client_id,
        })
        .map_err(|error| error.to_string()),
        _ => Err(format!("unsupported account provider: {provider}")),
    }
}

fn compose_blocking_credential_inspection(
    options: &AccountOptions,
) -> Result<Box<dyn kairos_integration::blocking::AccountCredentialInspectionConnection>, String> {
    let provider = normalized_provider(&options.provider);
    let product = account_product(options)?;
    let key = options.api_key.expose_secret().to_owned();
    let secret = options.secret.expose_secret().to_owned();
    if provider != "binance" {
        return Err(format!(
            "{provider} does not expose blocking credential inspection"
        ));
    }
    match product {
        AccountProduct::Spot => {
            binance::blocking::spot_credential_inspection(key, secret, options.base_url.clone())
        }
        AccountProduct::CrossMargin | AccountProduct::IsolatedMargin => {
            binance::blocking::margin_credential_inspection(
                binance_product(product)?,
                key,
                secret,
                options.base_url.clone(),
            )
        }
        AccountProduct::UsdMFutures | AccountProduct::CoinMFutures => {
            binance::blocking::futures_credential_inspection(
                binance_product(product)?,
                key,
                secret,
                options.base_url.clone(),
            )
        }
        AccountProduct::Options => {
            binance::blocking::options_credential_inspection(key, secret, options.base_url.clone())
        }
        AccountProduct::Equity => unreachable!(),
    }
    .map_err(|error| error.to_string())
}

fn compose_blocking_market_profile(
    options: &AccountOptions,
) -> Result<Option<Box<dyn AccountMarketProfileConnection + Send>>, String> {
    if normalized_provider(&options.provider) != "binance"
        || account_product(options)? != AccountProduct::Spot
    {
        return Ok(None);
    }
    binance::blocking::spot_market_profile(
        options.api_key.expose_secret().to_owned(),
        options.secret.expose_secret().to_owned(),
        options.base_url.clone(),
    )
    .map(Some)
    .map_err(|error| error.to_string())
}

#[cfg(test)]
mod secret_tests {
    use super::{
        account_product, binance_endpoint_family, binance_rest_base_url,
        compose_binance_async_account_application, compose_ibkr_async_account_application,
        compose_okx_async_account_application, okx_instrument_type, AccountOptions,
        AccountSegmentBinding,
    };

    fn binding(value: &str) -> AccountSegmentBinding {
        AccountSegmentBinding::new(value, value)
    }

    fn options() -> AccountOptions {
        AccountOptions {
            provider: "binance".into(),
            product: "spot".into(),
            api_key: "api-key-secret".into(),
            secret: "api-secret".into(),
            passphrase: "passphrase-secret".into(),
            base_url: "https://example.test".into(),
            account_id: "account".into(),
            segment: "spot".into(),
            environment: "paper".into(),
            account_model: None,
            initial_balances: Vec::new(),
            host: "127.0.0.1".into(),
            port: 4002,
            client_id: 0,
            isolated_margin_symbol: None,
            reference_database: None,
        }
    }

    #[test]
    fn account_options_debug_redacts_credentials() {
        let options = options();
        let output = format!("{options:?}");
        assert!(!output.contains("api-key-secret"));
        assert!(!output.contains("api-secret"));
        assert!(!output.contains("passphrase-secret"));
    }

    #[tokio::test(flavor = "current_thread")]
    async fn binance_spot_server_composes_one_shared_async_principal_context() {
        let composition = compose_binance_async_account_application(
            &options(),
            &[binding("spot")],
            None,
            "ws://127.0.0.1:1/ws-api/v3",
            None,
            "test-egress",
        )
        .unwrap();
        assert_eq!(composition.async_account_streams.len(), 1);
        assert_eq!(composition.application.async_source_counts(), (1, 1));
        assert_eq!(composition.provider, "binance");
    }

    #[tokio::test(flavor = "current_thread")]
    async fn account_segment_key_is_not_parsed_as_provider_product() {
        let composition = compose_binance_async_account_application(
            &options(),
            &[AccountSegmentBinding::new("cash-main", "spot")],
            None,
            "ws://127.0.0.1:1/ws-api/v3",
            None,
            "test-egress",
        )
        .unwrap();

        assert_eq!(composition.application.async_source_counts(), (1, 1));
    }

    #[tokio::test(flavor = "current_thread")]
    async fn binance_spot_and_funding_share_one_principal_without_fake_funding_stream() {
        let composition = compose_binance_async_account_application(
            &options(),
            &[binding("spot"), binding("funding")],
            None,
            "ws://127.0.0.1:1/ws-api/v3",
            None,
            "test-egress",
        )
        .unwrap();
        assert_eq!(composition.async_account_streams.len(), 1);
        assert_eq!(composition.application.async_source_counts(), (2, 1));
    }

    #[tokio::test(flavor = "current_thread")]
    async fn binance_derivative_and_margin_sources_use_only_migrated_async_capabilities() {
        for (segment, stream_count) in [
            ("cross_margin", 1),
            ("usd_m_futures", 1),
            ("coin_m_futures", 1),
            ("options", 1),
        ] {
            let composition = compose_binance_async_account_application(
                &options(),
                &[binding(segment)],
                None,
                "ws://127.0.0.1:1/ws-api/v3",
                None,
                "test-egress",
            )
            .unwrap();
            assert_eq!(
                composition.async_account_streams.len(),
                stream_count,
                "{segment}"
            );
            assert_eq!(composition.application.async_source_counts(), (1, 0));
        }
    }

    #[test]
    fn binance_default_rest_endpoint_follows_product_family() {
        let mut options = options();
        options.base_url = "https://api.binance.com".into();
        assert_eq!(
            binance_rest_base_url(&options, "usd-m-futures"),
            "https://fapi.binance.com"
        );
        assert_eq!(
            binance_rest_base_url(&options, "coin-m-futures"),
            "https://dapi.binance.com"
        );
        assert_eq!(
            binance_rest_base_url(&options, "options"),
            "https://eapi.binance.com"
        );
    }

    #[test]
    fn provider_product_vocabulary_is_not_cross_normalized() {
        assert!(binance_endpoint_family("swap").is_err());
        assert!(binance_endpoint_family("futures").is_err());
        assert!(okx_instrument_type("usd-m-futures").is_err());
        assert!(okx_instrument_type("coin-m-futures").is_err());

        let mut value = options();
        value.product = "swap".into();
        assert!(account_product(&value).is_err());
        value.product = "margin".into();
        assert!(account_product(&value).is_err());
    }

    #[test]
    fn binance_account_rejects_segments_from_different_endpoint_families() {
        let error = compose_binance_async_account_application(
            &options(),
            &[binding("spot"), binding("usd_m_futures")],
            None,
            "ws://127.0.0.1:1/ws-api/v3",
            None,
            "test-egress",
        )
        .err()
        .expect("mixed endpoint families must fail");
        assert!(error.contains("one REST endpoint family"));
    }

    #[test]
    fn binance_isolated_margin_requires_and_uses_account_owned_provider_symbol() {
        let missing = compose_binance_async_account_application(
            &options(),
            &[binding("isolated_margin")],
            None,
            "ws://127.0.0.1:1/ws-api/v3",
            None,
            "test-egress",
        )
        .err()
        .expect("isolated margin without provider symbol must fail");
        assert!(missing.contains("values.isolated_margin_symbol"));

        let mut configured = options();
        configured.isolated_margin_symbol = Some("btcusdt".into());
        let composition = compose_binance_async_account_application(
            &configured,
            &[binding("isolated_margin")],
            None,
            "ws://127.0.0.1:1/ws-api/v3",
            None,
            "test-egress",
        )
        .unwrap();
        assert_eq!(composition.async_account_streams.len(), 1);
        assert_eq!(composition.application.async_source_counts(), (1, 0));
    }

    #[tokio::test(flavor = "current_thread")]
    async fn okx_segments_share_one_native_principal_and_project_async_profiles() {
        let mut options = options();
        options.provider = "okx".into();
        options.passphrase = "passphrase".into();
        let composition = compose_okx_async_account_application(
            &options,
            &[binding("spot"), binding("swap").with_trading_mode("cross")],
            None,
            Some("ws://127.0.0.1:1"),
            None,
            "test-egress",
        )
        .unwrap();
        assert_eq!(composition.provider, "okx");
        assert_eq!(composition.async_account_streams.len(), 2);
        assert_eq!(composition.application.async_source_counts(), (2, 2));
    }

    #[test]
    fn okx_account_keeps_margin_product_and_trading_mode_independent() {
        let mut options = options();
        options.provider = "okx".into();
        options.passphrase = "passphrase".into();
        let missing = compose_okx_async_account_application(
            &options,
            &[AccountSegmentBinding::new("margin-main", "margin")],
            None,
            None,
            None,
            "test-egress",
        )
        .err()
        .unwrap();
        assert!(missing.contains("requires explicit trading_mode"));

        compose_okx_async_account_application(
            &options,
            &[AccountSegmentBinding::new("margin-main", "margin").with_trading_mode("isolated")],
            None,
            None,
            None,
            "test-egress",
        )
        .unwrap();
    }

    #[test]
    fn ibkr_account_projects_snapshot_and_events_from_one_native_session() {
        let mut options = options();
        options.provider = "ibkr".into();
        options.product = "equity".into();
        options.segment = "equity".into();
        let composition =
            compose_ibkr_async_account_application(&options, &[binding("equity")], None).unwrap();
        assert_eq!(composition.provider, "ibkr");
        assert_eq!(composition.async_account_streams.len(), 1);
        assert_eq!(composition.application.async_source_counts(), (1, 0));
    }
}
