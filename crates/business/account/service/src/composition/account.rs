use std::path::PathBuf;

use crate::application::{AccountApplication, AccountProcess, AccountSnapshotPublisher};
use crate::composition::empty_snapshot;
use crate::domain::{
    AccountSegment, AccountSnapshot, AssetId, Balance, ExternalAccountIdentity, SegmentKey,
    SignedQuantity,
};
use crate::services::integration::{
    async_account_market_profile_channel, async_account_read_channel, AccountAsyncEventSource,
    AccountEventStream, AccountMarketProfileGateway, AccountSnapshotGateway,
};
use crate::services::persistence::JsonAccountStore;
use kairos_integration::application::{
    AsyncAccountCredentialInspectionConnection, ExternalAccountCredentialProfile,
};
use kairos_integration::blocking::{
    AccountMarketProfileConnection, AccountReadConnection, BufferedIntegrationAccountStream,
};
use kairos_integration::participants::binance::ConnectionDomain as BinanceConnectionDomain;
use kairos_integration::participants::binance::{
    BinanceConnection, BinanceConnectionConfig, BinancePrincipalConfig, BinancePrincipalConnection,
    BinancePrincipalOrderQuotaAllocation, BinanceQuotaAllocation, BinanceSharedQuotaConfig,
    BinanceSpotChannelConfig,
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
}

pub struct AccountComposition {
    pub application: AccountApplication,
    pub provider: String,
    async_account_streams: Vec<AccountAsyncEventSource>,
    async_provider_tasks: Vec<tokio::task::JoinHandle<()>>,
}

impl AccountComposition {
    pub fn try_add_async_account_stream(
        &mut self,
        options: &AccountOptions,
        websocket_api_url: &str,
        segment_key: &str,
        shared_quota_ledger_path: Option<PathBuf>,
        egress_scope_id: &str,
    ) -> Result<bool, String> {
        let Some(source) = compose_async_account_stream(
            options,
            websocket_api_url,
            segment_key,
            shared_quota_ledger_path,
            egress_scope_id,
        )?
        else {
            return Ok(false);
        };
        self.async_account_streams.push(source);
        Ok(true)
    }

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
                .with_async_provider_tasks(self.async_provider_tasks)
        })
    }
}

fn connect_binance_principal(
    options: &AccountOptions,
    binding_id: impl Into<String>,
    shared_quota_ledger_path: Option<PathBuf>,
    egress_scope_id: &str,
) -> Result<BinancePrincipalConnection, String> {
    let provider = BinanceConnection::connect(BinanceConnectionConfig {
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

fn okx_instrument_type(segment: &str) -> Result<OkxInstrumentType, String> {
    match segment
        .trim()
        .to_ascii_lowercase()
        .replace('_', "-")
        .as_str()
    {
        "spot" => Ok(OkxInstrumentType::Spot),
        "cross-margin" | "isolated-margin" | "margin" => Ok(OkxInstrumentType::Margin),
        "swap" | "usd-m-futures" => Ok(OkxInstrumentType::Swap),
        "futures" | "coin-m-futures" => Ok(OkxInstrumentType::Futures),
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

/// Compose the provider-native Binance Spot async account channel. Other
/// products remain on their legacy adapters until their own provider slice is
/// migrated; they are never disguised as Binance Spot.
fn compose_async_account_stream(
    options: &AccountOptions,
    websocket_api_url: &str,
    segment_key: &str,
    shared_quota_ledger_path: Option<PathBuf>,
    egress_scope_id: &str,
) -> Result<Option<AccountAsyncEventSource>, String> {
    if normalized_provider(&options.provider) != "binance"
        || !options.product.trim().eq_ignore_ascii_case("spot")
    {
        return Ok(None);
    }
    let private = connect_binance_principal(
        options,
        format!("account.binance.spot.{segment_key}"),
        shared_quota_ledger_path,
        egress_scope_id,
    )?;
    private
        .spot_account_events(
            segment_key.to_owned(),
            &BinanceSpotChannelConfig {
                websocket_api_url: websocket_api_url.to_owned(),
                event_queue_capacity: 256,
            },
        )
        .map(AccountAsyncEventSource::BinanceSpot)
        .map(Some)
        .map_err(|error| error.to_string())
}

/// Compose Binance Spot and Funding account domains from one principal
/// connection. Readers share signer, clock, HTTP scheduling and quota;
/// only Spot projects a private event channel.
pub fn compose_binance_async_account_application(
    options: &AccountOptions,
    segments: &[String],
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
    if let Some(segment) = segments.iter().find(|segment| {
        !matches!(
            segment.trim().to_ascii_lowercase().as_str(),
            "spot" | "funding"
        )
    }) {
        return Err(format!(
            "Binance async composition does not yet support segment: {segment}"
        ));
    }
    let private = connect_binance_principal(
        options,
        "account.binance",
        shared_quota_ledger_path,
        egress_scope_id,
    )?;
    let identity = ExternalAccountIdentity::new("binance", options.account_id.clone())
        .map_err(|error| error.to_string())?;
    let mut account_segments = Vec::with_capacity(segments.len());
    let mut sources = std::collections::BTreeMap::new();
    let mut streams = Vec::new();
    let mut tasks = Vec::with_capacity(segments.len());
    for configured_segment in segments {
        let segment_key = configured_segment.trim().to_ascii_lowercase();
        account_segments.push(AccountSegment {
            identity: identity.clone(),
            segment_key: SegmentKey::new(segment_key.clone()).map_err(|error| error.to_string())?,
            environment: options.environment.clone(),
            account_model: Some(
                options
                    .account_model
                    .clone()
                    .unwrap_or_else(|| "no_margin".into()),
            ),
        });
        let (read_proxy, read_task) = if segment_key == "spot" {
            let stream = private
                .spot_account_events(
                    segment_key.clone(),
                    &BinanceSpotChannelConfig {
                        websocket_api_url: websocket_api_url.to_owned(),
                        event_queue_capacity: 256,
                    },
                )
                .map(AccountAsyncEventSource::BinanceSpot)
                .map_err(|error| error.to_string())?;
            streams.push(stream);
            async_account_read_channel(private.spot_descriptor(), private.spot_account_read())?
        } else {
            let read = private.funding_account_read();
            async_account_read_channel(read.descriptor().clone(), read)?
        };
        sources.insert(
            segment_key,
            Box::new(read_proxy) as Box<dyn AccountReadConnection + Send>,
        );
        tasks.push(read_task);
    }
    let source = AccountSnapshotGateway::integration(sources);
    let application = AccountApplication::with_dependencies(
        account_segments,
        source,
        state.map(JsonAccountStore::new),
    )
    .map_err(|error| error.to_string())?;
    Ok(AccountComposition {
        application,
        provider: "binance".into(),
        async_account_streams: streams,
        async_provider_tasks: tasks,
    })
}

/// Compose all configured OKX trading-account segments from one provider and
/// principal context. `InstrumentType` remains an OKX request filter; every
/// projected capability belongs to `okx::ConnectionDomain::Trading`.
pub fn compose_okx_async_account_application(
    options: &AccountOptions,
    segments: &[String],
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
    let instrument_types = segments
        .iter()
        .map(|segment| okx_instrument_type(segment))
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
    let mut tasks = Vec::with_capacity(segments.len().saturating_mul(2));
    for (configured_segment, instrument_type) in segments.iter().zip(instrument_types) {
        let segment_key = configured_segment.trim().to_ascii_lowercase();
        account_segments.push(AccountSegment {
            identity: identity.clone(),
            segment_key: SegmentKey::new(segment_key.clone()).map_err(|error| error.to_string())?,
            environment: options.environment.clone(),
            account_model: options.account_model.clone(),
        });

        let read = principal.trading_account(instrument_type);
        let (read_proxy, read_task) = async_account_read_channel(read.descriptor().clone(), read)?;
        sources.insert(
            segment_key.clone(),
            Box::new(read_proxy) as Box<dyn AccountReadConnection + Send>,
        );
        tasks.push(read_task);

        let profile = principal.trading_account_market_profile(instrument_type);
        let (profile_proxy, profile_task) =
            async_account_market_profile_channel(profile.descriptor().clone(), profile)?;
        profile_sources.insert(
            segment_key.clone(),
            Box::new(profile_proxy) as Box<dyn AccountMarketProfileConnection + Send>,
        );
        tasks.push(profile_task);
        if let Some(websocket_url) = private_websocket_url {
            streams.push(AccountAsyncEventSource::OkxTrading(
                principal
                    .trading_account_events(
                        instrument_type,
                        segment_key,
                        &OkxPrivateChannelConfig {
                            websocket_url: websocket_url.to_owned(),
                            event_queue_capacity: 256,
                        },
                    )
                    .map_err(|error| error.to_string())?,
            ));
        }
    }
    let mut application = AccountApplication::with_dependencies(
        account_segments,
        AccountSnapshotGateway::integration(sources),
        state.map(JsonAccountStore::new),
    )
    .map_err(|error| error.to_string())?;
    application.attach_market_profile_source(AccountMarketProfileGateway::new(profile_sources));
    Ok(AccountComposition {
        application,
        provider: "okx".into(),
        async_account_streams: streams,
        async_provider_tasks: tasks,
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
    if provider == "binance" && product == "funding" {
        let principal = connect_binance_principal(
            options,
            "account.binance.funding.inspect",
            shared_quota_ledger_path.clone(),
            egress_scope_id,
        )?;
        let mut inspection = principal.funding_credential_inspection();
        return inspection
            .inspect_credential()
            .await
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

    // Provider slices which have not yet acquired an async participant-native
    // handle remain isolated on a blocking worker. The Account runtime owns
    // this scheduling decision; Integration does not create or inject a
    // runtime behind the connection.
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

pub fn compose_account_application(
    options: &AccountOptions,
    state: Option<PathBuf>,
) -> Result<AccountComposition, String> {
    compose_account_application_for_segments(options, std::slice::from_ref(&options.segment), state)
}

/// Compose one account actor with every configured segment for the account.
///
/// A provider connection remains the integration-owned source, while the
/// account actor owns the complete set of segment state.  Keeping this
/// function at the account composition boundary lets CLI and server use the
/// same multi-segment path without making integration depend on account
/// configuration.
pub fn compose_account_application_for_segments(
    options: &AccountOptions,
    segments: &[String],
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
            .map(|segment_key| AccountSegment {
                identity: identity.clone(),
                segment_key: SegmentKey::new(segment_key.clone())
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
                let mut snapshot = empty_snapshot(segment.clone());
                snapshot.balances = options
                    .initial_balances
                    .iter()
                    .map(|value| parse_initial_balance(value))
                    .collect::<Result<Vec<_>, _>>()?;
                Ok((segment.clone(), snapshot))
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
            async_provider_tasks: Vec::new(),
        });
    }
    let identity = ExternalAccountIdentity::new(&provider, options.account_id.clone())
        .map_err(|error| error.to_string())?;
    let mut account_segments = Vec::with_capacity(segments.len());
    let mut sources = std::collections::BTreeMap::new();
    let mut profile_sources = std::collections::BTreeMap::new();
    for segment_key in segments {
        let mut segment_options = options.clone();
        segment_options.product = segment_key.clone();
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
    let mut application = AccountApplication::with_dependencies(
        account_segments,
        AccountSnapshotGateway::integration(sources),
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
        async_provider_tasks: Vec::new(),
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

/// Attach a normalized Integration stream during concrete composition.
pub fn attach_account_stream(
    application: &mut AccountApplication,
    stream: BufferedIntegrationAccountStream,
) {
    application.attach_stream(AccountEventStream::new(stream));
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
        asset_code: kairos_domain_types::Currency::new(asset_code)
            .map_err(|error| error.to_string())?,
        total,
        available: None,
        locked: None,
        borrowed: None,
        interest: None,
    })
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
            "cross-margin" | "margin" => Ok(AccountProduct::CrossMargin),
            "isolated-margin" => Ok(AccountProduct::IsolatedMargin),
            "options" => Ok(AccountProduct::Options),
            "usd-m-futures" | "swap" => Ok(AccountProduct::UsdMFutures),
            "coin-m-futures" | "futures" => Ok(AccountProduct::CoinMFutures),
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

pub fn compose_blocking_account_stream(
    options: &AccountOptions,
    websocket_endpoint: Option<&str>,
    segment_key: &str,
) -> Result<Box<dyn kairos_integration::blocking::AccountEventStreamConnection>, String> {
    let provider = normalized_provider(&options.provider);
    if provider == "ibkr" {
        return ibkr::blocking::account_stream(
            &ibkr::IbkrConnectionConfig {
                host: options.host.clone(),
                port: options.port,
                client_id: options.client_id,
            },
            options.account_id.clone(),
            segment_key,
        )
        .map_err(|error| error.to_string());
    }
    if provider != "binance" {
        return Err(format!("unsupported account stream provider: {provider}"));
    }
    let endpoint = websocket_endpoint
        .ok_or_else(|| format!("Binance {segment_key} account stream endpoint is required"))?;
    let product = account_product(options)?;
    let key = options.api_key.expose_secret().to_owned();
    let secret = options.secret.expose_secret().to_owned();
    match product {
        AccountProduct::Spot => binance::blocking::spot_account_stream(
            key,
            secret,
            options.base_url.clone(),
            endpoint,
            segment_key,
        ),
        AccountProduct::CrossMargin | AccountProduct::IsolatedMargin => {
            binance::blocking::margin_account_stream(
                binance_product(product)?,
                key,
                secret,
                options.base_url.clone(),
                endpoint,
                segment_key,
            )
        }
        AccountProduct::UsdMFutures | AccountProduct::CoinMFutures => {
            binance::blocking::futures_account_stream(
                binance_product(product)?,
                key,
                secret,
                options.base_url.clone(),
                endpoint,
                segment_key,
            )
        }
        AccountProduct::Options | AccountProduct::Equity => {
            Err(kairos_integration::application::IntegrationError::UnsupportedOperation)
        }
    }
    .map_err(|error| error.to_string())
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
        compose_binance_async_account_application, compose_okx_async_account_application,
        AccountOptions,
    };

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
            &["spot".into()],
            None,
            "ws://127.0.0.1:1/ws-api/v3",
            None,
            "test-egress",
        )
        .unwrap();
        assert_eq!(composition.async_account_streams.len(), 1);
        assert_eq!(composition.async_provider_tasks.len(), 1);
        assert_eq!(composition.provider, "binance");
        for task in composition.async_provider_tasks.drain(..) {
            task.abort();
            let _ = task.await;
        }
    }

    #[tokio::test(flavor = "current_thread")]
    async fn binance_spot_and_funding_share_one_principal_without_fake_funding_stream() {
        let mut composition = compose_binance_async_account_application(
            &options(),
            &["spot".into(), "funding".into()],
            None,
            "ws://127.0.0.1:1/ws-api/v3",
            None,
            "test-egress",
        )
        .unwrap();
        assert_eq!(composition.async_account_streams.len(), 1);
        assert_eq!(composition.async_provider_tasks.len(), 2);
        for task in composition.async_provider_tasks.drain(..) {
            task.abort();
            let _ = task.await;
        }
    }

    #[tokio::test(flavor = "current_thread")]
    async fn okx_segments_share_one_native_principal_and_project_async_profiles() {
        let mut options = options();
        options.provider = "okx".into();
        options.passphrase = "passphrase".into();
        let mut composition = compose_okx_async_account_application(
            &options,
            &["spot".into(), "swap".into()],
            None,
            Some("ws://127.0.0.1:1"),
            None,
            "test-egress",
        )
        .unwrap();
        assert_eq!(composition.provider, "okx");
        assert_eq!(composition.async_account_streams.len(), 2);
        assert_eq!(composition.async_provider_tasks.len(), 4);
        for task in composition.async_provider_tasks.drain(..) {
            task.abort();
            let _ = task.await;
        }
    }
}
