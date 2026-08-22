use std::path::PathBuf;

use kairos_conflux::{
    AccountCredentialQuery, AccountQuery, BinanceCredential,
    BinancePortfolioMarginProRestConnection, BinancePortfolioMarginRestConnection,
    BinanceRestConfig, BinanceUserWebSocketConfig, ConnectionKey, ExternalAccountCredentialProfile,
    ExternalAccountIdentity as IntegrationAccountIdentity, ExternalAccountSegment,
    ExternalAccountSnapshot, IbkrAccountQueryConfig, IbkrAccountStreamConfig, OkxCredential,
    OkxPrivateRestConfig, OkxPrivateWebSocketConfig, OkxRestConfig, OkxWebSocketConfig,
};
use secrecy::SecretString;

use crate::application::AccountApplication;
use crate::composition::empty_snapshot;
use crate::domain::{
    AccountSegment, AccountSnapshot, AssetId, Balance, ExternalAccountIdentity, SegmentKey,
    SignedQuantity,
};
use crate::services::integration::{
    AccountAsyncEventSource, AccountAsyncSnapshotConnection, AccountInstrumentResolver,
    AccountSnapshotGateway,
};
use crate::services::persistence::JsonAccountStore;

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
    /// Reference's canonical SQLite database, opened read-only by its contract client.
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
    system: kairos_conflux::ConfluxSystem,
    instrument_resolver: AccountInstrumentResolver,
}

impl AccountComposition {
    /// Transfers every provider-native connection into Conflux's named,
    /// concrete resource universe. Keys are Account segment keys so the Actor
    /// can route normalized facts without a second capability registry.
    pub fn into_conflux(
        mut self,
        refresh_interval: std::time::Duration,
    ) -> Result<(AccountApplication, kairos_conflux::ConfluxSystem), String> {
        let system = self.system;
        if matches!(self.provider.as_str(), "paper" | "simulated") {
            self.application.enable_simulation();
        }
        self.application
            .configure_conflux(refresh_interval, self.instrument_resolver)?;
        Ok((self.application, system))
    }
}

fn account_system(
    connections: std::collections::BTreeMap<String, AccountAsyncSnapshotConnection>,
    streams: Vec<AccountAsyncEventSource>,
) -> Result<kairos_conflux::ConfluxSystem, String> {
    let mut system = kairos_conflux::ConfluxSystem::new();
    for (key, connection) in connections {
        connection.into_conflux(key, &mut system.connections())?;
    }
    for stream in streams {
        stream.into_conflux(&mut system.connections())?;
    }
    Ok(system)
}

fn binance_credential(options: &AccountOptions) -> BinanceCredential {
    BinanceCredential {
        principal_id: options.account_id.clone(),
        api_key: options.api_key.clone(),
        secret: options.secret.clone(),
    }
}

fn binance_rest_config(
    options: &AccountOptions,
    _connection_key: impl Into<String>,
) -> BinanceRestConfig {
    BinanceRestConfig {
        environment: options.environment.clone(),
        endpoint: options.base_url.clone(),
        credential: Some(binance_credential(options)),
    }
}

fn binance_user_config(
    options: &AccountOptions,
    _connection_key: impl Into<String>,
    rest_endpoint: String,
    websocket_endpoint: impl Into<String>,
    segment_key: impl Into<String>,
) -> BinanceUserWebSocketConfig {
    BinanceUserWebSocketConfig {
        environment: options.environment.clone(),
        rest_endpoint,
        websocket_endpoint: websocket_endpoint.into(),
        credential: binance_credential(options),
        event_capacity: 256,
        segment_key: segment_key.into(),
    }
}

fn okx_credential(options: &AccountOptions) -> OkxCredential {
    OkxCredential {
        principal_id: options.account_id.clone(),
        api_key: options.api_key.clone(),
        secret: options.secret.clone(),
        passphrase: options.passphrase.clone(),
    }
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
        },
        ("binance", "usd-m-futures") => Ok("https://fapi.binance.com"),
        ("binance", "coin-m-futures") => Ok("https://dapi.binance.com"),
        ("binance", "options") => Ok("https://eapi.binance.com"),
        ("okx", "spot" | "margin" | "swap" | "futures" | "option" | "options") => {
            Ok("https://www.okx.com")
        },
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

/// Perform one provider query without creating a process, projection, or
/// Conflux runtime. Top-level Account commands use this path; connected reads
/// remain scoped to a launch component.
pub async fn query_direct_account_snapshot(
    options: &AccountOptions,
    binding: &AccountSegmentBinding,
) -> Result<ExternalAccountSnapshot, String> {
    let provider = normalized_provider(&options.provider);
    let product = normalized_segment(&binding.provider_product);
    let segment_key =
        SegmentKey::new(binding.segment_key.clone()).map_err(|error| error.to_string())?;
    let segment = ExternalAccountSegment {
        identity: IntegrationAccountIdentity::new(provider.clone(), options.account_id.clone())?,
        segment_key,
        environment: options.environment.clone(),
        account_model: options.account_model.clone(),
    };
    let key = format!("account.direct.{provider}.{}", binding.segment_key);
    if provider == "binance"
        && product == "usd-m-futures"
        && options
            .account_model
            .as_deref()
            .is_some_and(|value| normalized_segment(value) == "portfolio-margin")
    {
        return match query_binance_portfolio_snapshot(options, key.clone(), &segment).await {
            Ok(snapshot) => Ok(snapshot),
            Err(portfolio_error) => query_binance_portfolio_pro_snapshot(options, key, &segment)
                .await
                .map_err(|pro_error| {
                    format!(
                        "Binance Portfolio Margin query failed: {portfolio_error}; Portfolio Margin Pro query failed: {pro_error}"
                    )
                }),
        };
    }
    if provider == "binance"
        && product == "usd-m-futures"
        && options
            .account_model
            .as_deref()
            .is_some_and(|value| normalized_segment(value) == "portfolio-margin-pro")
    {
        return query_binance_portfolio_pro_snapshot(options, key, &segment).await;
    }
    let connection = match provider.as_str() {
        "binance" => {
            let family = binance_endpoint_family(&product)?;
            let mut segment_options = options.clone();
            segment_options.base_url = binance_rest_base_url(options, family);
            let config = binance_rest_config(&segment_options, key.clone());
            match product.as_str() {
                "spot" => AccountAsyncSnapshotConnection::BinanceSpot(config),
                "funding" => AccountAsyncSnapshotConnection::BinanceFunding(config),
                "cross-margin" | "isolated-margin" => {
                    AccountAsyncSnapshotConnection::BinanceMargin(config)
                },
                "usd-m-futures" => AccountAsyncSnapshotConnection::BinanceUsdM(config),
                "coin-m-futures" => AccountAsyncSnapshotConnection::BinanceCoinM(config),
                "options" => AccountAsyncSnapshotConnection::BinanceOptions(config),
                _ => unreachable!("validated Binance Account product"),
            }
        },
        "okx" => AccountAsyncSnapshotConnection::OkxTrading(OkxPrivateRestConfig {
            connection: OkxRestConfig {
                environment: options.environment.clone(),
                endpoint: options.base_url.clone(),
            },
            credential: okx_credential(options),
        }),
        "ibkr" => AccountAsyncSnapshotConnection::Ibkr(IbkrAccountQueryConfig {
            environment: options.environment.clone(),
            host: options.host.clone(),
            port: options.port,
            client_id: options.client_id,
            account_id: options.account_id.clone(),
        }),
        _ => return Err(format!("unsupported direct Account provider: {provider}")),
    };
    let snapshot = connection.fetch(key.clone(), &segment).await?;
    if provider == "binance"
        && product == "usd-m-futures"
        && snapshot.balances.is_empty()
        && snapshot.positions.is_empty()
        && options.environment.eq_ignore_ascii_case("live")
    {
        if let Ok(portfolio_snapshot) =
            query_binance_portfolio_snapshot(options, key.clone(), &segment).await
        {
            if !portfolio_snapshot.balances.is_empty() || !portfolio_snapshot.positions.is_empty() {
                return Ok(portfolio_snapshot);
            }
        }
        if let Ok(portfolio_snapshot) =
            query_binance_portfolio_pro_snapshot(options, key, &segment).await
        {
            if !portfolio_snapshot.balances.is_empty() || !portfolio_snapshot.positions.is_empty() {
                return Ok(portfolio_snapshot);
            }
        }
    }
    Ok(snapshot)
}

async fn query_binance_portfolio_pro_snapshot(
    options: &AccountOptions,
    key: String,
    segment: &ExternalAccountSegment,
) -> Result<ExternalAccountSnapshot, String> {
    let mut portfolio_options = options.clone();
    portfolio_options.base_url = "https://api.binance.com".into();
    let connection_key = format!("{key}.portfolio-pro");
    let mut portfolio = BinancePortfolioMarginProRestConnection::new(
        ConnectionKey::new(connection_key.clone())?,
        binance_rest_config(&portfolio_options, connection_key),
    )
    .map_err(|error| error.to_string())?;
    portfolio
        .fetch_account(segment)
        .await
        .map_err(|error| error.to_string())
}

async fn query_binance_portfolio_snapshot(
    options: &AccountOptions,
    key: String,
    segment: &ExternalAccountSegment,
) -> Result<ExternalAccountSnapshot, String> {
    let mut portfolio_options = options.clone();
    portfolio_options.base_url = "https://papi.binance.com".into();
    let connection_key = format!("{key}.portfolio");
    let mut portfolio = BinancePortfolioMarginRestConnection::new(
        ConnectionKey::new(connection_key.clone())?,
        binance_rest_config(&portfolio_options, connection_key),
    )
    .map_err(|error| error.to_string())?;
    portfolio
        .fetch_account(segment)
        .await
        .map_err(|error| error.to_string())
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
    _shared_quota_ledger_path: Option<PathBuf>,
    _egress_scope_id: &str,
) -> Result<AccountComposition, String> {
    if normalized_provider(&options.provider) != "binance" {
        return Err("Binance async composition requires provider=binance".into());
    }
    if segments.is_empty() {
        return Err("Binance async composition requires at least one segment".into());
    }
    for segment in segments {
        binance_endpoint_family(&segment.provider_product)?;
    }
    let identity = ExternalAccountIdentity::new("binance", options.account_id.clone())
        .map_err(|error| error.to_string())?;
    let mut account_segments = Vec::with_capacity(segments.len());
    let mut sources = std::collections::BTreeMap::new();
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
                let rest_endpoint = binance_rest_base_url(options, "spot");
                streams.push(AccountAsyncEventSource::BinanceSpot {
                    segment_key: SegmentKey::new(segment_key.clone())
                        .expect("validated Account segment key"),
                    parameters: binance_user_config(
                        options,
                        format!("account.binance.spot.{segment_key}"),
                        rest_endpoint.clone(),
                        websocket_api_url,
                        segment_key.clone(),
                    ),
                });
                let mut rest_options = options.clone();
                rest_options.base_url = rest_endpoint;
                AccountAsyncSnapshotConnection::BinanceSpot(binance_rest_config(
                    &rest_options,
                    format!("account.binance.spot.rest.{segment_key}"),
                ))
            },
            "funding" => {
                let mut rest_options = options.clone();
                rest_options.base_url = binance_rest_base_url(options, "spot");
                AccountAsyncSnapshotConnection::BinanceFunding(binance_rest_config(
                    &rest_options,
                    format!("account.binance.funding.rest.{segment_key}"),
                ))
            },
            "cross-margin" => {
                let rest_endpoint = binance_rest_base_url(options, "spot");
                streams.push(AccountAsyncEventSource::BinanceMargin {
                    segment_key: SegmentKey::new(segment_key.clone())
                        .expect("validated Account segment key"),
                    parameters: binance_user_config(
                        options,
                        format!("account.binance.cross-margin.{segment_key}"),
                        rest_endpoint.clone(),
                        "wss://stream.binance.com:9443/ws",
                        segment_key.clone(),
                    ),
                });
                let mut rest_options = options.clone();
                rest_options.base_url = rest_endpoint;
                AccountAsyncSnapshotConnection::BinanceMargin(binance_rest_config(
                    &rest_options,
                    format!("account.binance.cross-margin.rest.{segment_key}"),
                ))
            },
            "isolated-margin" => {
                let isolated_margin_symbol =
                    options.isolated_margin_symbol.as_deref().ok_or_else(|| {
                        "Binance isolated-margin Account requires values.isolated_margin_symbol"
                            .to_string()
                    })?;
                let rest_endpoint = binance_rest_base_url(options, "spot");
                streams.push(AccountAsyncEventSource::BinanceMargin {
                    segment_key: SegmentKey::new(segment_key.clone())
                        .expect("validated Account segment key"),
                    parameters: binance_user_config(
                        options,
                        format!(
                            "account.binance.isolated-margin.{isolated_margin_symbol}.{segment_key}"
                        ),
                        rest_endpoint.clone(),
                        "wss://stream.binance.com:9443/ws",
                        segment_key.clone(),
                    ),
                });
                let mut rest_options = options.clone();
                rest_options.base_url = rest_endpoint;
                AccountAsyncSnapshotConnection::BinanceMargin(binance_rest_config(
                    &rest_options,
                    format!("account.binance.isolated-margin.rest.{segment_key}"),
                ))
            },
            "usd-m-futures" => {
                let rest_endpoint = binance_rest_base_url(options, "usd-m-futures");
                streams.push(AccountAsyncEventSource::BinanceUsdM {
                    segment_key: SegmentKey::new(segment_key.clone())
                        .expect("validated Account segment key"),
                    parameters: binance_user_config(
                        options,
                        format!("account.binance.usdm.{segment_key}"),
                        rest_endpoint.clone(),
                        "wss://fstream.binance.com/ws",
                        segment_key.clone(),
                    ),
                });
                let mut rest_options = options.clone();
                rest_options.base_url = rest_endpoint;
                AccountAsyncSnapshotConnection::BinanceUsdM(binance_rest_config(
                    &rest_options,
                    format!("account.binance.usdm.rest.{segment_key}"),
                ))
            },
            "coin-m-futures" => {
                let rest_endpoint = binance_rest_base_url(options, "coin-m-futures");
                streams.push(AccountAsyncEventSource::BinanceCoinM {
                    segment_key: SegmentKey::new(segment_key.clone())
                        .expect("validated Account segment key"),
                    parameters: binance_user_config(
                        options,
                        format!("account.binance.coinm.{segment_key}"),
                        rest_endpoint.clone(),
                        "wss://dstream.binance.com/ws",
                        segment_key.clone(),
                    ),
                });
                let mut rest_options = options.clone();
                rest_options.base_url = rest_endpoint;
                AccountAsyncSnapshotConnection::BinanceCoinM(binance_rest_config(
                    &rest_options,
                    format!("account.binance.coinm.rest.{segment_key}"),
                ))
            },
            "options" => {
                let rest_endpoint = binance_rest_base_url(options, "options");
                streams.push(AccountAsyncEventSource::BinanceOptions {
                    segment_key: SegmentKey::new(segment_key.clone())
                        .expect("validated Account segment key"),
                    parameters: binance_user_config(
                        options,
                        format!("account.binance.options.{segment_key}"),
                        rest_endpoint.clone(),
                        "wss://nbstream.binance.com/eoptions/private/stream",
                        segment_key.clone(),
                    ),
                });
                let mut rest_options = options.clone();
                rest_options.base_url = rest_endpoint;
                AccountAsyncSnapshotConnection::BinanceOptions(binance_rest_config(
                    &rest_options,
                    format!("account.binance.options.rest.{segment_key}"),
                ))
            },
            _ => unreachable!("validated Binance Account segment"),
        };
        sources.insert(segment_key, read);
    }
    let instrument_resolver = load_instrument_resolver(options)?;
    let application = AccountApplication::with_async_dependencies(
        account_segments,
        state.map(JsonAccountStore::new),
    )
    .map_err(|error| error.to_string())?;
    let system = account_system(sources, streams)?;
    Ok(AccountComposition {
        application,
        provider: "binance".into(),
        system,
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
    _shared_quota_ledger_path: Option<PathBuf>,
    _egress_scope_id: &str,
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
            ("spot", None | Some("cash")) => {},
            ("margin" | "swap" | "futures" | "option" | "options", Some("cross" | "isolated")) => {
            },
            ("spot", Some(_)) => return Err("OKX spot Account requires cash trading_mode".into()),
            ("margin" | "swap" | "futures" | "option" | "options", None) => {
                return Err(format!(
                    "OKX {product} Account segment requires explicit trading_mode (cross or isolated)"
                ));
            },
            (_, Some(value)) => return Err(format!("unsupported OKX trading mode: {value}")),
            _ => {},
        }
    }
    let identity = ExternalAccountIdentity::new("okx", options.account_id.clone())
        .map_err(|error| error.to_string())?;
    let mut account_segments = Vec::with_capacity(segments.len());
    let mut sources = std::collections::BTreeMap::new();
    let mut streams = Vec::new();
    for configured_segment in segments {
        let segment_key = configured_segment.segment_key.clone();
        account_segments.push(AccountSegment {
            identity: identity.clone(),
            segment_key: SegmentKey::new(segment_key.clone()).map_err(|error| error.to_string())?,
            environment: options.environment.clone(),
            account_model: options.account_model.clone(),
        });

        sources.insert(
            segment_key.clone(),
            AccountAsyncSnapshotConnection::OkxTrading(OkxPrivateRestConfig {
                connection: OkxRestConfig {
                    environment: options.environment.clone(),
                    endpoint: options.base_url.clone(),
                },
                credential: okx_credential(options),
            }),
        );
        if let Some(websocket_url) = private_websocket_url {
            streams.push(AccountAsyncEventSource::OkxTrading {
                segment_key: SegmentKey::new(segment_key.clone())
                    .expect("validated Account segment key"),
                parameters: OkxPrivateWebSocketConfig {
                    connection: OkxWebSocketConfig {
                        environment: options.environment.clone(),
                        endpoint: websocket_url.to_owned(),
                        event_capacity: 256,
                    },
                    rest_endpoint: options.base_url.clone(),
                    credential: okx_credential(options),
                    segment_key: segment_key.clone(),
                    trading_mode: configured_segment
                        .trading_mode
                        .clone()
                        .unwrap_or_else(|| "cash".into()),
                },
            });
        }
    }
    let instrument_resolver = load_instrument_resolver(options)?;
    let application = AccountApplication::with_async_dependencies(
        account_segments,
        state.map(JsonAccountStore::new),
    )
    .map_err(|error| error.to_string())?;
    let system = account_system(sources, streams)?;
    Ok(AccountComposition {
        application,
        provider: "okx".into(),
        system,
        instrument_resolver,
    })
}

/// Compose IBKR's library-backed account query and event stream as explicit
/// virtual connections with distinct TWS/Gateway client identities.
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
    let query = IbkrAccountQueryConfig {
        environment: options.environment.clone(),
        host: options.host.clone(),
        port: options.port,
        client_id: options.client_id,
        account_id: options.account_id.clone(),
    };
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
    let application = AccountApplication::with_async_dependencies(
        account_segments,
        state.map(JsonAccountStore::new),
    )
    .map_err(|error| error.to_string())?;
    let source = IbkrAccountStreamConfig {
        environment: options.environment.clone(),
        host: options.host.clone(),
        port: options.port,
        client_id: options.client_id.saturating_add(1),
        account_id: options.account_id.clone(),
        segment_key: segment_key.clone(),
    };
    let system = account_system(
        std::collections::BTreeMap::from([(
            segment_key.clone(),
            AccountAsyncSnapshotConnection::Ibkr(query),
        )]),
        vec![AccountAsyncEventSource::Ibkr {
            segment_key: SegmentKey::new(segment_key.clone())
                .expect("validated Account segment key"),
            parameters: source,
        }],
    )?;
    Ok(AccountComposition {
        application,
        provider: "ibkr".into(),
        system,
        instrument_resolver,
    })
}

/// Inspect an account credential using the participant capability selected by
/// Account composition. Integration exposes the capability; it does not own
/// this cross-participant business selection.
pub async fn inspect_account_credential(
    options: &AccountOptions,
    _shared_quota_ledger_path: Option<PathBuf>,
    _egress_scope_id: &str,
) -> Result<ExternalAccountCredentialProfile, String> {
    let provider = normalized_provider(&options.provider);
    let product = options
        .product
        .trim()
        .to_ascii_lowercase()
        .replace('_', "-");
    if provider == "binance" {
        binance_endpoint_family(&product)?;
        return Err(format!(
            "Binance {product} does not expose AccountCredentialQuery"
        ));
    }
    if provider == "okx" {
        let key = ConnectionKey::new("account.okx.inspect")?;
        let mut system = kairos_conflux::ConfluxSystem::new();
        let mut connections = system.connections();
        connections
            .okx_private_rest
            .create(
                key.clone(),
                OkxPrivateRestConfig {
                    connection: OkxRestConfig {
                        environment: options.environment.clone(),
                        endpoint: options.base_url.clone(),
                    },
                    credential: okx_credential(options),
                },
            )
            .map_err(|error| error.to_string())?;
        return connections
            .okx_private_rest
            .get(&key)
            .map_err(|error| error.to_string())?
            .inspect_credential()
            .await
            .map_err(|error| error.to_string());
    }

    Err(format!("{provider} does not expose AccountCredentialQuery"))
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
    if segments.is_empty() {
        return Err("at least one account segment is required".into());
    }
    let provider = normalized_provider(&options.provider);
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
    Ok(AccountComposition {
        application,
        provider,
        system: kairos_conflux::ConfluxSystem::new(),
        instrument_resolver: AccountInstrumentResolver::default(),
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
    let mut application = AccountApplication::with_dependencies(
        segments,
        AccountSnapshotGateway::memory(snapshots),
        state.map(JsonAccountStore::new),
    )
    .map_err(|error| error.to_string())?;
    application.enable_simulation();
    Ok(application)
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
        asset_code: kairos_primitives::reference::Currency::new(asset_code)
            .map_err(|error| error.to_string())?,
        total,
        available: None,
        locked: None,
        borrowed: None,
        interest: None,
    })
}

fn load_instrument_resolver(options: &AccountOptions) -> Result<AccountInstrumentResolver, String> {
    let _ = options;
    Ok(AccountInstrumentResolver::default())
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

#[cfg(test)]
mod secret_tests {
    use super::{
        AccountOptions, AccountSegmentBinding, account_product, binance_endpoint_family,
        binance_rest_base_url, compose_binance_async_account_application,
        compose_ibkr_async_account_application, compose_okx_async_account_application,
    };

    fn account_stream_count(system: &mut kairos_conflux::ConfluxSystem) -> usize {
        let connections = system.connections();
        connections.binance_spot_user_websocket.keys().len()
            + connections.binance_margin_user_websocket.keys().len()
            + connections.binance_usdm_user_websocket.keys().len()
            + connections.binance_coinm_user_websocket.keys().len()
            + connections.binance_options_user_websocket.keys().len()
            + connections.okx_private_websocket.keys().len()
            + connections.ibkr_account_stream.keys().len()
    }

    fn account_query_count(system: &mut kairos_conflux::ConfluxSystem) -> usize {
        let connections = system.connections();
        connections.binance_spot_rest.keys().len()
            + connections.binance_funding_rest.keys().len()
            + connections.binance_margin_rest.keys().len()
            + connections.binance_usdm_rest.keys().len()
            + connections.binance_coinm_rest.keys().len()
            + connections.binance_options_rest.keys().len()
            + connections.okx_private_rest.keys().len()
            + connections.ibkr_account_query.keys().len()
    }

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
        let mut composition = compose_binance_async_account_application(
            &options(),
            &[binding("spot")],
            None,
            "ws://127.0.0.1:1/ws-api/v3",
            None,
            "test-egress",
        )
        .unwrap();
        assert_eq!(account_stream_count(&mut composition.system), 1);
        assert_eq!(account_query_count(&mut composition.system), 1);
        assert_eq!(composition.provider, "binance");
    }

    #[tokio::test(flavor = "current_thread")]
    async fn account_connections_transfer_into_named_conflux_resources() {
        let composition = compose_binance_async_account_application(
            &options(),
            &[binding("spot"), binding("funding")],
            None,
            "ws://127.0.0.1:1/ws-api/v3",
            None,
            "test-egress",
        )
        .unwrap();

        let (application, mut system) = composition
            .into_conflux(std::time::Duration::from_secs(30))
            .unwrap();

        let connections = system.connections();
        assert_eq!(connections.binance_spot_rest.keys().len(), 1);
        assert_eq!(connections.binance_funding_rest.keys().len(), 1);
        assert_eq!(connections.binance_spot_user_websocket.keys().len(), 1);
        assert_eq!(
            application.runtime_mode(),
            crate::application::AccountRuntimeMode::Live
        );
    }

    #[tokio::test(flavor = "current_thread")]
    async fn account_segment_key_is_not_parsed_as_provider_product() {
        let mut composition = compose_binance_async_account_application(
            &options(),
            &[AccountSegmentBinding::new("cash-main", "spot")],
            None,
            "ws://127.0.0.1:1/ws-api/v3",
            None,
            "test-egress",
        )
        .unwrap();

        assert_eq!(account_query_count(&mut composition.system), 1);
    }

    #[tokio::test(flavor = "current_thread")]
    async fn binance_spot_and_funding_share_one_principal_without_fake_funding_stream() {
        let mut composition = compose_binance_async_account_application(
            &options(),
            &[binding("spot"), binding("funding")],
            None,
            "ws://127.0.0.1:1/ws-api/v3",
            None,
            "test-egress",
        )
        .unwrap();
        assert_eq!(account_stream_count(&mut composition.system), 1);
        assert_eq!(account_query_count(&mut composition.system), 2);
    }

    #[tokio::test(flavor = "current_thread")]
    async fn binance_derivative_and_margin_sources_use_only_migrated_async_capabilities() {
        for (segment, stream_count) in [
            ("cross_margin", 1),
            ("usd_m_futures", 1),
            ("coin_m_futures", 1),
            ("options", 1),
        ] {
            let mut composition = compose_binance_async_account_application(
                &options(),
                &[binding(segment)],
                None,
                "ws://127.0.0.1:1/ws-api/v3",
                None,
                "test-egress",
            )
            .unwrap();
            assert_eq!(
                account_stream_count(&mut composition.system),
                stream_count,
                "{segment}"
            );
            assert_eq!(account_query_count(&mut composition.system), 1);
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

        let mut value = options();
        value.product = "swap".into();
        assert!(account_product(&value).is_err());
        value.product = "margin".into();
        assert!(account_product(&value).is_err());
    }

    #[test]
    fn binance_account_supports_segments_from_different_endpoint_families() {
        let mut composition = compose_binance_async_account_application(
            &options(),
            &[
                binding("spot"),
                binding("usd_m_futures"),
                binding("funding"),
            ],
            None,
            "ws://127.0.0.1:1/ws-api/v3",
            None,
            "test-egress",
        )
        .expect("one Account must compose all configured Binance segments");
        assert_eq!(account_query_count(&mut composition.system), 3);
        assert_eq!(account_stream_count(&mut composition.system), 2);
    }

    #[test]
    fn binance_isolated_margin_requires_and_uses_configured_symbol() {
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
        let mut composition = compose_binance_async_account_application(
            &configured,
            &[binding("isolated_margin")],
            None,
            "ws://127.0.0.1:1/ws-api/v3",
            None,
            "test-egress",
        )
        .unwrap();
        assert_eq!(account_stream_count(&mut composition.system), 1);
        assert_eq!(account_query_count(&mut composition.system), 1);
    }

    #[tokio::test(flavor = "current_thread")]
    async fn okx_segments_share_one_native_principal_for_account_facts() {
        let mut options = options();
        options.provider = "okx".into();
        options.passphrase = "passphrase".into();
        let mut composition = compose_okx_async_account_application(
            &options,
            &[binding("spot"), binding("swap").with_trading_mode("cross")],
            None,
            Some("ws://127.0.0.1:1"),
            None,
            "test-egress",
        )
        .unwrap();
        assert_eq!(composition.provider, "okx");
        assert_eq!(account_stream_count(&mut composition.system), 2);
        assert_eq!(account_query_count(&mut composition.system), 2);
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
        let mut composition =
            compose_ibkr_async_account_application(&options, &[binding("equity")], None).unwrap();
        assert_eq!(composition.provider, "ibkr");
        assert_eq!(account_stream_count(&mut composition.system), 1);
        assert_eq!(account_query_count(&mut composition.system), 1);
    }
}
