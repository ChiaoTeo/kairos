use std::path::PathBuf;

use crate::application::protocol::{
    AccountMarketProfileSource, AccountSnapshotSource, AccountStreamSource,
};
use crate::application::{AccountApplication, AccountMarketProfile, AccountMarketProfileRequest};
use crate::composition::{empty_snapshot, InMemoryAccountSource, JsonAccountStore};
use crate::domain::{
    AccountEvent, AccountFill, AccountModel, AccountSegment, AccountSnapshot, AccountStatus,
    Balance, DecimalValue, ExternalAccountIdentity, MarginMode, OpenOrder, OrderEvent, OrderStatus,
    Position, PositionMode,
};
use kairos_integration::application::{
    AccountMarketProfileConnection, AccountReadConnection, BufferedIntegrationAccountStream,
    ConnectionSpec, EarnConnection, ExternalAccountCredentialProfile, ExternalMarketProfileRequest,
    TransferConnection,
};
use kairos_integration::domain::{
    AccessScope, ExternalAccountEvent, ExternalAccountModel, ExternalAccountSegment,
    ExternalAccountStatus, ExternalBalance, ExternalDecimal, ExternalMarginMode,
    ExternalOrderStatus, ExternalPositionMode, IntegrationCapability, ProductFamily, TransportKind,
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

pub struct IntegrationAccountSource<C> {
    connection: C,
}

impl<C> IntegrationAccountSource<C> {
    pub fn new(connection: C) -> Self {
        Self { connection }
    }
}

impl<C: AccountReadConnection + Send> AccountSnapshotSource for IntegrationAccountSource<C> {
    fn fetch(&mut self, segment: &AccountSegment) -> Result<AccountSnapshot, String> {
        let external = ExternalAccountSegment {
            identity: kairos_integration::domain::account::ExternalAccountIdentity {
                broker: segment.identity.broker.clone(),
                account_id: segment.identity.account_id.clone(),
            },
            segment_key: segment.segment_key.clone(),
            environment: segment.environment.clone(),
            account_model: segment.account_model.clone(),
        };
        self.connection
            .fetch_account(&external)
            .map(map_snapshot)
            .map_err(|error| error.to_string())
    }
}

/// Routes each configured account segment to its own integration connection.
/// A single venue account may expose spot, margin, and derivatives through
/// different endpoints; reusing the first connection for every segment would
/// silently read the wrong product.
pub struct MultiIntegrationAccountSource {
    connections: std::collections::BTreeMap<String, Box<dyn AccountReadConnection + Send>>,
}

impl MultiIntegrationAccountSource {
    pub fn new(
        connections: std::collections::BTreeMap<String, Box<dyn AccountReadConnection + Send>>,
    ) -> Self {
        Self { connections }
    }
}

impl AccountSnapshotSource for MultiIntegrationAccountSource {
    fn fetch(&mut self, segment: &AccountSegment) -> Result<AccountSnapshot, String> {
        let connection = self
            .connections
            .get_mut(&segment.segment_key)
            .ok_or_else(|| format!("account segment is not configured: {}", segment.segment_key))?;
        let external = ExternalAccountSegment {
            identity: kairos_integration::domain::account::ExternalAccountIdentity {
                broker: segment.identity.broker.clone(),
                account_id: segment.identity.account_id.clone(),
            },
            segment_key: segment.segment_key.clone(),
            environment: segment.environment.clone(),
            account_model: segment.account_model.clone(),
        };
        connection
            .fetch_account(&external)
            .map(map_snapshot)
            .map_err(|error| error.to_string())
    }
}

pub struct IntegrationAccountProfileSource<C> {
    connection: C,
}

impl<C> IntegrationAccountProfileSource<C> {
    pub fn new(connection: C) -> Self {
        Self { connection }
    }
}

impl<C: AccountMarketProfileConnection + Send> AccountMarketProfileSource
    for IntegrationAccountProfileSource<C>
{
    fn fetch_profile(
        &mut self,
        request: &AccountMarketProfileRequest,
    ) -> Result<AccountMarketProfile, String> {
        let external = ExternalMarketProfileRequest {
            account_id: request.account_id.clone(),
            segment_key: request.segment_key.clone(),
            market_id: request.market_id.clone(),
            source_symbol: request.source_symbol.clone(),
        };
        self.connection
            .fetch_market_profile(&external)
            .map(map_profile)
            .map_err(|error| error.to_string())
    }
}

pub struct MultiIntegrationAccountProfileSource {
    connections: std::collections::BTreeMap<String, Box<dyn AccountMarketProfileConnection + Send>>,
}

impl MultiIntegrationAccountProfileSource {
    pub fn new(
        connections: std::collections::BTreeMap<
            String,
            Box<dyn AccountMarketProfileConnection + Send>,
        >,
    ) -> Self {
        Self { connections }
    }
}

impl AccountMarketProfileSource for MultiIntegrationAccountProfileSource {
    fn fetch_profile(
        &mut self,
        request: &AccountMarketProfileRequest,
    ) -> Result<AccountMarketProfile, String> {
        let connection = self
            .connections
            .get_mut(&request.segment_key)
            .ok_or_else(|| format!("account segment is not configured: {}", request.segment_key))?;
        let external = ExternalMarketProfileRequest {
            account_id: request.account_id.clone(),
            segment_key: request.segment_key.clone(),
            market_id: request.market_id.clone(),
            source_symbol: request.source_symbol.clone(),
        };
        connection
            .fetch_market_profile(&external)
            .map(map_profile)
            .map_err(|error| error.to_string())
    }
}

pub struct IntegrationAccountStreamAdapter {
    stream: BufferedIntegrationAccountStream,
}

impl IntegrationAccountStreamAdapter {
    pub fn new(stream: BufferedIntegrationAccountStream) -> Self {
        Self { stream }
    }
}

impl AccountStreamSource for IntegrationAccountStreamAdapter {
    fn next_event(&mut self) -> Result<Option<AccountEvent>, String> {
        self.stream.next_event().map(|event| event.map(map_event))
    }
}

fn decimal(value: ExternalDecimal) -> DecimalValue {
    DecimalValue::new(value.mantissa, value.scale)
}

fn map_balance(value: ExternalBalance) -> Balance {
    Balance {
        asset_id: value.asset_id,
        asset_code: value.asset_code,
        total: decimal(value.total),
        available: value.available.map(decimal),
        locked: value.locked.map(decimal),
        borrowed: value.borrowed.map(decimal),
        interest: value.interest.map(decimal),
    }
}

fn map_position(value: kairos_integration::domain::ExternalPosition) -> Position {
    Position {
        instrument_id: value.instrument_id,
        market_id: value.market_id,
        quantity: decimal(value.quantity),
        average_price: value.average_price.map(decimal),
        mark_price: value.mark_price.map(decimal),
        unrealized_pnl: value.unrealized_pnl.map(decimal),
        realized_pnl: value.realized_pnl.map(decimal),
        updated_at_unix_nanos: value.updated_at_unix_nanos,
    }
}

fn map_snapshot(value: kairos_integration::domain::ExternalAccountSnapshot) -> AccountSnapshot {
    AccountSnapshot {
        segment_key: value.segment_key,
        balances: value.balances.into_iter().map(map_balance).collect(),
        collateral: value.collateral.into_iter().map(map_balance).collect(),
        positions: value.positions.into_iter().map(map_position).collect(),
        open_orders: value.open_orders.into_iter().map(map_open_order).collect(),
        status: map_status(value.status),
        observed_at_unix_nanos: value.observed_at_unix_nanos,
        equity: value.equity.map(decimal),
        initial_equity: value.initial_equity.map(decimal),
        net_profit: value.net_profit.map(decimal),
        account_model: value.account_model.map(map_model),
        margin_mode: value.margin_mode.map(map_margin),
        position_mode: value.position_mode.map(map_position_mode),
        partial: value.partial,
    }
}

fn map_open_order(value: kairos_integration::domain::ExternalOpenOrder) -> OpenOrder {
    OpenOrder {
        order_id: value.order_id,
        venue_order_id: value.venue_order_id,
        instrument_id: value.instrument_id,
        side: value.side,
        quantity: decimal(value.quantity),
        filled_quantity: decimal(value.filled_quantity),
        status: value.status,
    }
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
fn map_order_status(value: ExternalOrderStatus) -> OrderStatus {
    match value {
        ExternalOrderStatus::Acknowledged => OrderStatus::Acknowledged,
        ExternalOrderStatus::PartiallyFilled => OrderStatus::PartiallyFilled,
        ExternalOrderStatus::Filled => OrderStatus::Filled,
        ExternalOrderStatus::Canceled => OrderStatus::Canceled,
        ExternalOrderStatus::Rejected => OrderStatus::Rejected,
        ExternalOrderStatus::Expired => OrderStatus::Expired,
        ExternalOrderStatus::Unknown => OrderStatus::Unknown,
    }
}
fn map_event(value: ExternalAccountEvent) -> AccountEvent {
    match value {
        ExternalAccountEvent::Batch(values) => {
            AccountEvent::Batch(values.into_iter().map(map_event).collect())
        }
        ExternalAccountEvent::Snapshot(value) => AccountEvent::Snapshot(map_snapshot(value)),
        ExternalAccountEvent::Order(value) => AccountEvent::Order(OrderEvent {
            order_id: value.order_id,
            status: map_order_status(value.status),
            venue_order_id: value.venue_order_id,
            filled_quantity: value.filled_quantity.map(decimal),
            occurred_at_unix_nanos: value.occurred_at_unix_nanos,
            reason: value.reason,
        }),
        ExternalAccountEvent::Fill(value) => AccountEvent::Fill(AccountFill {
            fill_id: Some(value.fill_id),
            order_id: Some(value.order_id),
            segment_key: value.segment_key,
            instrument_id: value.instrument_id,
            quantity: decimal(value.quantity),
            price: decimal(value.price),
            side: if value.side.eq_ignore_ascii_case("sell") {
                crate::domain::FillSide::Sell
            } else {
                crate::domain::FillSide::Buy
            },
            settlement_asset: None,
            settlement_delta: None,
            fee_asset: value.fee_asset,
            fee_amount: value.fee_amount.map(decimal),
            occurred_at_unix_nanos: value.occurred_at_unix_nanos,
        }),
    }
}
fn map_profile(
    value: kairos_integration::application::ExternalMarketProfile,
) -> AccountMarketProfile {
    AccountMarketProfile {
        account_id: value.account_id,
        segment_key: value.segment_key,
        market_id: value.market_id,
        account_model: value.account_model.map(map_model),
        margin_mode: value.margin_mode,
        position_mode: value.position_mode,
        maker_fee: value.maker_fee.map(decimal),
        taker_fee: value.taker_fee.map(decimal),
        fee_currency: value.fee_currency,
        fee_discount: value.fee_discount.map(decimal),
        fee_tier: value.fee_tier,
        source: value.source,
        observed_at_unix_nanos: value.observed_at_unix_nanos,
    }
}

pub fn compose_account_application(
    options: &AccountOptions,
    state: Option<PathBuf>,
) -> Result<AccountComposition, String> {
    compose_account_application_for_segments(options, &[options.segment.clone()], state)
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
        let identity = ExternalAccountIdentity::new(&provider, options.account_id.clone())?;
        let account_segments: Vec<_> = segments
            .iter()
            .map(|segment_key| AccountSegment {
                identity: identity.clone(),
                segment_key: segment_key.clone(),
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
        let source = InMemoryAccountSource {
            snapshots: segments
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
                .into_iter()
                .collect::<Result<std::collections::BTreeMap<_, _>, String>>()?,
        };
        let application = AccountApplication::with_dependencies(
            account_segments,
            Box::new(source),
            state.map(|path| Box::new(JsonAccountStore::new(path)) as _),
        )
        .map_err(|error| error.to_string())?;
        return Ok(AccountComposition {
            application,
            integration: Integration::new(),
            provider,
            product: ProductFamily::Spot,
        });
    }
    let identity = ExternalAccountIdentity::new(&provider, options.account_id.clone())?;
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
            segment_key: segment_key.clone(),
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
        Box::new(MultiIntegrationAccountSource::new(sources)),
        state.map(|path| Box::new(JsonAccountStore::new(path)) as _),
    )
    .map_err(|error| error.to_string())?;
    if !profile_sources.is_empty() {
        application.attach_market_profile_source(Box::new(
            MultiIntegrationAccountProfileSource::new(profile_sources),
        ));
    }
    Ok(AccountComposition {
        application,
        integration,
        provider,
        product: primary_product,
    })
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
    let (negative, quantity) = quantity
        .strip_prefix('-')
        .map(|value| (true, value))
        .unwrap_or((false, quantity.strip_prefix('+').unwrap_or(quantity)));
    let (whole, fraction) = quantity.split_once('.').unwrap_or((quantity, ""));
    if whole.is_empty() && fraction.is_empty()
        || !whole.chars().all(|value| value.is_ascii_digit())
        || !fraction.chars().all(|value| value.is_ascii_digit())
        || fraction.len() > u8::MAX as usize
    {
        return Err(format!("invalid initial balance quantity: {quantity}"));
    }
    let scale = fraction.len() as u8;
    let digits = format!("{whole}{fraction}");
    let mut mantissa = digits
        .parse::<i64>()
        .map_err(|_| format!("initial balance quantity overflows i64: {quantity}"))?;
    if negative {
        mantissa = mantissa
            .checked_neg()
            .ok_or_else(|| format!("initial balance quantity overflows i64: {quantity}"))?;
    }
    Ok(Balance {
        asset_id: format!("asset:{}", asset_code.to_ascii_lowercase()),
        asset_code,
        total: DecimalValue::new(mantissa, scale),
        ..Default::default()
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
