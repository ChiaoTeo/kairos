use std::path::PathBuf;

use crate::application::AccountApplication;
use crate::composition::empty_snapshot;
use crate::domain::{
    AccountSegment, AccountSnapshot, AssetId, Balance, Decimal, ExternalAccountIdentity, SegmentKey,
};
use crate::services::integration::{
    AccountEventStream, AccountMarketProfileGateway, AccountSnapshotGateway,
};
use crate::services::persistence::JsonAccountStore;
use kairos_integration::application::{
    AccountMarketProfileConnection, AccountReadConnection, BufferedIntegrationAccountStream,
    ConnectionSpec, EarnConnection, ExternalAccountCredentialProfile, TransferConnection,
};
use kairos_integration::domain::{
    AccessScope, IntegrationCapability, ProductFamily, TransportKind,
};
use kairos_integration::Integration;

#[path = "account_registry.rs"]
mod account_registry;

pub use account_registry::{
    AccountBindingRecord, AccountCredentialBinding, AccountRegistry, CredentialRecord,
    CredentialStore, TradeLockRecord,
};

#[derive(Clone, Debug)]
pub struct AccountOptions {
    pub provider: String,
    pub product: String,
    pub api_key: String,
    pub secret: String,
    pub passphrase: String,
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
    pub integration: Integration,
    pub provider: String,
    pub product: ProductFamily,
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
            integration: Integration::new(),
            provider,
            product: ProductFamily::Spot,
        });
    }
    let identity = ExternalAccountIdentity::new(&provider, options.account_id.clone())
        .map_err(|error| error.to_string())?;
    let mut account_segments = Vec::with_capacity(segments.len());
    let mut sources = std::collections::BTreeMap::new();
    let mut profile_sources = std::collections::BTreeMap::new();
    let mut primary_integration = None;
    let mut primary_product = ProductFamily::Spot;
    for segment_key in segments {
        let mut segment_options = options.clone();
        segment_options.product = segment_key.clone();
        let (segment_integration, product) = compose_integration(&segment_options)?;
        let product_name = segment_options.product.trim().to_ascii_lowercase();
        let is_funding = provider == "binance" && product_name == "funding";
        let account_model = options.account_model.clone().unwrap_or_else(|| {
            if is_funding || product == ProductFamily::Spot {
                "no_margin".into()
            } else if matches!(
                product,
                ProductFamily::CrossMargin | ProductFamily::IsolatedMargin
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
        let connection = segment_integration
            .connect_account(&ConnectionSpec {
                connection_id: format!("account.{}.{}.rest", provider, product_name),
                route: if provider == "ibkr" {
                    kairos_integration::IntegrationRoute::broker("ibkr")
                } else {
                    kairos_integration::IntegrationRoute::exchange(provider.clone())
                },
                product: if is_funding { None } else { Some(product) },
                access: AccessScope::Private,
                transport: TransportKind::Rest,
                capability: IntegrationCapability::AccountRead,
                credential_id: Some(provider.clone()),
                asset_type: None,
            })
            .map_err(|error| error.to_string())?;
        sources.insert(
            segment_key.clone(),
            Box::new(connection) as Box<dyn AccountReadConnection + Send>,
        );
        if let Ok(connection) =
            segment_integration.connect_account_market_profile(&ConnectionSpec {
                connection_id: format!("account.{}.{}.profile", provider, product_name),
                route: if provider == "ibkr" {
                    kairos_integration::IntegrationRoute::broker("ibkr")
                } else {
                    kairos_integration::IntegrationRoute::exchange(provider.clone())
                },
                product: if is_funding { None } else { Some(product) },
                access: AccessScope::Private,
                transport: TransportKind::Rest,
                capability: IntegrationCapability::AccountMarketProfileRead,
                credential_id: Some(provider.clone()),
                asset_type: None,
            })
        {
            profile_sources.insert(
                segment_key.clone(),
                Box::new(connection) as Box<dyn AccountMarketProfileConnection + Send>,
            );
        }
        if primary_integration.is_none() {
            primary_product = product;
            primary_integration = Some(segment_integration);
        }
    }
    let integration = primary_integration.ok_or("at least one account connection is required")?;
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
        integration,
        provider,
        product: primary_product,
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
    let total = Decimal::parse(quantity)
        .map_err(|_| format!("invalid initial balance quantity: {quantity}"))?;
    Ok(Balance {
        asset_id: AssetId::new(format!("asset:{}", asset_code.to_ascii_lowercase()))
            .map_err(|error| error.to_string())?,
        asset_code,
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

pub fn compose_integration(
    options: &AccountOptions,
) -> Result<(Integration, ProductFamily), String> {
    let provider = normalized_provider(&options.provider);
    let product = options
        .product
        .trim()
        .to_ascii_lowercase()
        .replace('_', "-");
    match provider.as_str() {
        "binance" => match product.as_str() {
            "spot" => Ok((
                Integration::new().with_binance_spot_account(
                    options.api_key.clone(),
                    options.secret.clone(),
                    options.base_url.clone(),
                ),
                ProductFamily::Spot,
            )),
            "funding" => Ok((
                Integration::new().with_binance_funding_account(
                    options.api_key.clone(),
                    options.secret.clone(),
                    options.base_url.clone(),
                ),
                ProductFamily::Spot,
            )),
            "cross-margin" | "margin" => Ok((
                Integration::new()
                    .with_binance_margin_account(
                        ProductFamily::CrossMargin,
                        options.api_key.clone(),
                        options.secret.clone(),
                        options.base_url.clone(),
                    )
                    .map_err(|error| error.to_string())?,
                ProductFamily::CrossMargin,
            )),
            "isolated-margin" => Ok((
                Integration::new()
                    .with_binance_margin_account(
                        ProductFamily::IsolatedMargin,
                        options.api_key.clone(),
                        options.secret.clone(),
                        options.base_url.clone(),
                    )
                    .map_err(|error| error.to_string())?,
                ProductFamily::IsolatedMargin,
            )),
            "options" => Ok((
                Integration::new().with_binance_options_account(
                    options.api_key.clone(),
                    options.secret.clone(),
                    options.base_url.clone(),
                ),
                ProductFamily::Options,
            )),
            "usd-m-futures" | "swap" => Ok((
                Integration::new()
                    .with_binance_futures_account(
                        ProductFamily::UsdMFutures,
                        options.api_key.clone(),
                        options.secret.clone(),
                        options.base_url.clone(),
                    )
                    .map_err(|error| error.to_string())?,
                ProductFamily::UsdMFutures,
            )),
            "coin-m-futures" | "futures" => Ok((
                Integration::new()
                    .with_binance_futures_account(
                        ProductFamily::CoinMFutures,
                        options.api_key.clone(),
                        options.secret.clone(),
                        options.base_url.clone(),
                    )
                    .map_err(|error| error.to_string())?,
                ProductFamily::CoinMFutures,
            )),
            _ => Err(format!("unsupported Binance account product: {product}")),
        },
        "okx" => {
            let product = match product.as_str() {
                "spot" => ProductFamily::Spot,
                "cross-margin" => ProductFamily::CrossMargin,
                "isolated-margin" => ProductFamily::IsolatedMargin,
                "swap" | "usd-m-futures" => ProductFamily::UsdMFutures,
                "futures" | "coin-m-futures" => ProductFamily::CoinMFutures,
                "options" => ProductFamily::Options,
                _ => return Err(format!("unsupported OKX account product: {product}")),
            };
            Ok((
                Integration::new()
                    .with_okx_account(
                        product,
                        options.api_key.clone(),
                        options.secret.clone(),
                        options.passphrase.clone(),
                        options.base_url.clone(),
                    )
                    .map_err(|error| error.to_string())?,
                product,
            ))
        }
        "ibkr" => {
            if !matches!(product.as_str(), "spot" | "equity") {
                return Err(format!("unsupported IBKR account product: {product}"));
            }
            Ok((
                Integration::new().with_ibkr_account(
                    options.host.clone(),
                    options.port,
                    options.client_id,
                ),
                ProductFamily::Spot,
            ))
        }
        _ => Err(format!("unsupported account provider: {provider}")),
    }
}

/// Inspect a live credential through a normalized integration capability.
/// Account administration owns the binding policy; integration only returns
/// provider-neutral discovery facts.
pub fn inspect_account_credential(
    options: &AccountOptions,
) -> Result<ExternalAccountCredentialProfile, String> {
    let (integration, product) = compose_integration(options)?;
    let provider = normalized_provider(&options.provider);
    let product_name = options
        .product
        .trim()
        .to_ascii_lowercase()
        .replace('_', "-");
    let is_funding = provider == "binance" && product_name == "funding";
    let mut connection = integration
        .connect_account_credential_inspection(&ConnectionSpec {
            connection_id: format!("account.{provider}.{product_name}.inspect"),
            route: if provider == "ibkr" {
                kairos_integration::IntegrationRoute::broker("ibkr")
            } else {
                kairos_integration::IntegrationRoute::exchange(provider.clone())
            },
            product: if is_funding { None } else { Some(product) },
            access: AccessScope::Private,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::AccountCredentialInspection,
            credential_id: Some("credential".into()),
            asset_type: None,
        })
        .map_err(|error| error.to_string())?;
    connection.inspect_credential()
}

pub fn compose_binance_transfer(
    options: &AccountOptions,
) -> Result<Box<dyn TransferConnection>, String> {
    Integration::new()
        .with_binance_transfer(
            options.api_key.clone(),
            options.secret.clone(),
            options.base_url.clone(),
        )
        .connect_transfer(&ConnectionSpec {
            connection_id: "account.binance.transfer".into(),
            route: kairos_integration::IntegrationRoute::exchange("binance"),
            product: None,
            access: AccessScope::Private,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::Transfer,
            credential_id: Some("binance".into()),
            asset_type: None,
        })
        .map_err(|error| error.to_string())
}

pub fn compose_binance_earn(options: &AccountOptions) -> Result<Box<dyn EarnConnection>, String> {
    Integration::new()
        .with_binance_earn(
            options.api_key.clone(),
            options.secret.clone(),
            options.base_url.clone(),
        )
        .connect_earn(&ConnectionSpec {
            connection_id: "account.binance.earn".into(),
            route: kairos_integration::IntegrationRoute::exchange("binance"),
            product: None,
            access: AccessScope::Private,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::Earn,
            credential_id: Some("binance".into()),
            asset_type: None,
        })
        .map_err(|error| error.to_string())
}
