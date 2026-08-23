use std::collections::BTreeMap;
use std::path::PathBuf;

use kairos_conflux::{
    CredentialRecord, CredentialStore, ExternalAccountCredentialProfile, ExternalFeeComponent,
    ExternalFeeSchedule, ExternalOrder,
};
use kairos_primitives::account::{AccountId, BrokerId, SegmentKey};
use kairos_primitives::decimal::DecimalParts;
use kairos_primitives::integration::ProviderId;
use kairos_primitives::reference::Currency;
use kairos_workspace::Workspace;
use serde::Serialize;

use crate::composition::account::{
    AccountOptions, AccountSegmentBinding, default_rest_endpoint, inspect_account_credential,
    query_direct_account_info, query_direct_account_profile, query_direct_account_snapshot,
    query_direct_earn_positions, query_direct_fee_schedule, query_direct_open_orders,
    query_direct_position_mode,
};
use crate::composition::registry::{
    AccountBindingRecord, AccountCredentialBinding, AccountRegistry,
};
use crate::domain::AccountModel;

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AccountQueryCompleteness {
    Complete,
    Partial,
    Unsupported,
    Unavailable,
    Unauthorized,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AccountOverviewResult {
    pub identity: AccountOverviewIdentity,
    pub connection: AccountOverviewConnection,
    pub profile: AccountOverviewProfile,
    pub permissions: AccountOverviewPermissions,
    pub commercial: AccountOverviewCommercial,
    pub facts: AccountOverviewFacts,
    pub health: AccountOverviewHealth,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AccountOverviewCommercial {
    pub vip_tier: Option<String>,
    pub bnb_fee_discount: Option<bool>,
    pub fee_summary_status: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AccountOverviewIdentity {
    pub account_id: AccountId,
    pub alias: String,
    pub broker: BrokerId,
    pub exchange: Option<String>,
    pub environment: String,
    pub masked_remote_identity: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AccountOverviewConnection {
    pub integration_provider: ProviderId,
    pub credential_bindings: Vec<AccountCredentialSummary>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AccountCredentialSummary {
    pub name: String,
    pub credential_id: String,
    pub role: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AccountOverviewProfile {
    pub configured_account_model: Option<String>,
    pub observed_account_model: Option<String>,
    pub provider_account_model: Option<String>,
    pub model_match: String,
    pub unified: Option<bool>,
    pub margin_mode: Option<String>,
    pub position_mode: Option<String>,
    pub segments: Vec<AccountSegmentProfileItem>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AccountSegmentProfileItem {
    pub segment: SegmentKey,
    pub source: String,
    pub freshness: String,
    pub completeness: AccountQueryCompleteness,
    pub observed_at_unix_nanos: Option<u64>,
    pub issue: Option<String>,
    pub configured_account_model: Option<String>,
    pub observed_account_model: Option<String>,
    pub provider_account_model: Option<String>,
    pub margin_mode: Option<String>,
    pub position_mode: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AccountOverviewPermissions {
    pub configured_credential_role: String,
    pub observed_permissions: BTreeMap<String, String>,
    pub effective_capabilities: Vec<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AccountOverviewFacts {
    pub non_zero_balance_count: u64,
    pub collateral_count: u64,
    pub position_count: u64,
    pub earn_holding_count: Option<u64>,
    pub open_order_count: Option<u64>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AccountOverviewHealth {
    pub source: String,
    pub mode: String,
    pub overall_status: String,
    pub freshness: String,
    pub completeness: AccountQueryCompleteness,
    pub segments_requested: u64,
    pub segments_succeeded: u64,
    pub observed_at_unix_nanos: Option<u64>,
    pub issues: Vec<AccountQueryError>,
}

/// Standalone Account CLI facade.
///
/// This facade owns one CLI invocation worth of workspace/config access. It
/// must not create Conflux, register RPC actors, publish projections, or read
/// launch-instance runtime state.
pub struct CliAccountApplication {
    pub registry_path: PathBuf,
    pub registry: AccountRegistry,
    pub credentials_path: PathBuf,
    pub credential_store: CredentialStore,
    provider_quota_ledger_path: PathBuf,
    reference_database: PathBuf,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AccountListResult {
    pub accounts: Vec<AccountListItem>,
    pub count: u64,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AccountListItem {
    pub account_id: AccountId,
    pub alias: String,
    pub broker: BrokerId,
    pub exchange: Option<String>,
    pub integration_provider: ProviderId,
    /// Deprecated compatibility alias for `integration_provider`.
    ///
    /// Account identity is carried by `broker`; callers must not interpret
    /// this field as the account's business owner.
    pub provider: ProviderId,
    pub environment: String,
    pub segments: Vec<SegmentKey>,
    pub products: Vec<String>,
    pub account_model: Option<String>,
    pub credential_id: Option<String>,
    pub configured_credential_role: String,
    pub capabilities: Vec<String>,
    pub status: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AccountTradingBinding {
    pub account_id: AccountId,
    pub remote_account_id: String,
    pub provider: ProviderId,
    pub environment: String,
    pub segment_key: SegmentKey,
    pub provider_product: String,
    pub trading_mode: Option<String>,
    pub credential_id: Option<String>,
    pub credential_role: String,
    pub base_url: String,
    pub host: String,
    pub port: u16,
    pub client_id: i32,
    pub isolated_symbol: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AccountBalancesResult {
    pub account_id: AccountId,
    pub source: String,
    pub mode: String,
    pub kind: String,
    pub segments_requested: u64,
    pub segments_succeeded: u64,
    pub completeness: AccountQueryCompleteness,
    pub observed_at_unix_nanos: Option<u64>,
    pub balances: Vec<AccountBalanceItem>,
    pub collateral: Vec<AccountBalanceItem>,
    pub outcomes: Vec<AccountSegmentQueryOutcome>,
    pub errors: Vec<AccountQueryError>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AccountQueryError {
    pub segment: SegmentKey,
    pub message: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AccountBalanceItem {
    pub segment: SegmentKey,
    pub role: String,
    pub asset: Currency,
    pub total: DecimalParts,
    pub available: Option<DecimalParts>,
    pub locked: Option<DecimalParts>,
    pub borrowed: Option<DecimalParts>,
    pub interest: Option<DecimalParts>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AccountPositionsResult {
    pub account_id: AccountId,
    pub source: String,
    pub mode: String,
    pub kind: String,
    pub segments_requested: u64,
    pub segments_succeeded: u64,
    pub completeness: AccountQueryCompleteness,
    pub observed_at_unix_nanos: Option<u64>,
    pub positions: Vec<AccountPositionItem>,
    pub outcomes: Vec<AccountSegmentQueryOutcome>,
    pub errors: Vec<AccountQueryError>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AccountPositionItem {
    pub segment: SegmentKey,
    pub symbol: String,
    pub instrument_type: Option<String>,
    pub side: String,
    pub quantity: DecimalParts,
    pub average_price: Option<DecimalParts>,
    pub mark_price: Option<DecimalParts>,
    pub unrealized_pnl: Option<DecimalParts>,
    pub realized_pnl: Option<DecimalParts>,
    pub margin_mode: Option<String>,
    pub position_mode: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AccountEarnHoldingsResult {
    pub account_id: AccountId,
    pub source: String,
    pub mode: String,
    pub kind: String,
    pub completeness: AccountQueryCompleteness,
    pub observed_at_unix_nanos: Option<u64>,
    pub holdings: Vec<AccountEarnHoldingItem>,
    pub outcomes: Vec<AccountSegmentQueryOutcome>,
    pub errors: Vec<AccountQueryError>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AccountEarnHoldingItem {
    pub segment: SegmentKey,
    pub participant_position_id: Option<String>,
    pub product_id: String,
    pub asset: Currency,
    pub family: String,
    pub principal: DecimalParts,
    pub accrued_rewards: Vec<AccountEarnRewardItem>,
    pub redeemable_amount: Option<DecimalParts>,
    pub liquidity: String,
    pub subscribed_at_unix_nanos: Option<u64>,
    pub matures_at_unix_nanos: Option<u64>,
    pub state: String,
    pub observed_at_unix_nanos: Option<u64>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AccountOpenOrdersResult {
    pub account_id: AccountId,
    pub source: String,
    pub mode: String,
    pub kind: String,
    pub completeness: AccountQueryCompleteness,
    pub segments_requested: u64,
    pub segments_succeeded: u64,
    pub observed_at_unix_nanos: Option<u64>,
    pub orders: Vec<AccountOpenOrderItem>,
    pub outcomes: Vec<AccountSegmentQueryOutcome>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AccountOpenOrderItem {
    pub segment: SegmentKey,
    pub order_id: String,
    pub client_order_id: Option<String>,
    pub symbol: String,
    pub side: String,
    pub order_type: String,
    pub status: String,
    pub quantity: DecimalParts,
    pub filled_quantity: DecimalParts,
    pub average_fill_price: Option<DecimalParts>,
    pub occurred_at_unix_nanos: Option<u64>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AccountSegmentQueryOutcome {
    pub segment: SegmentKey,
    pub outcome: AccountQueryCompleteness,
    pub message: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AccountFeesResult {
    pub account_id: AccountId,
    pub source: String,
    pub mode: String,
    pub kind: String,
    pub product: String,
    pub symbol: Option<String>,
    pub completeness: AccountQueryCompleteness,
    pub maker: Option<DecimalParts>,
    pub taker: Option<DecimalParts>,
    pub buyer: Option<DecimalParts>,
    pub seller: Option<DecimalParts>,
    pub standard: Option<AccountFeeComponent>,
    pub special: Option<AccountFeeComponent>,
    pub tax: Option<AccountFeeComponent>,
    pub discount: Option<AccountFeeDiscount>,
    pub rpi: Option<DecimalParts>,
    pub vip_tier: Option<String>,
    pub vip_tier_status: String,
    pub observed_at_unix_nanos: Option<u64>,
    pub issues: Vec<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AccountFeeComponent {
    pub maker: Option<DecimalParts>,
    pub taker: Option<DecimalParts>,
    pub buyer: Option<DecimalParts>,
    pub seller: Option<DecimalParts>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AccountFeeDiscount {
    pub enabled_for_account: Option<bool>,
    pub enabled_for_symbol: Option<bool>,
    pub asset: Option<String>,
    pub rate: Option<DecimalParts>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AccountEarnRewardItem {
    pub asset: Currency,
    pub amount: DecimalParts,
    pub component: Option<String>,
}

impl TryFrom<&AccountBindingRecord> for AccountListItem {
    type Error = kairos_primitives::DomainTypeError;

    fn try_from(record: &AccountBindingRecord) -> Result<Self, Self::Error> {
        Ok(Self {
            account_id: AccountId::new(record.account_id.clone())?,
            alias: record.alias.clone(),
            broker: BrokerId::new(record.broker.clone())?,
            exchange: record.exchange.clone(),
            integration_provider: ProviderId::new(record.integration_provider.clone())?,
            provider: ProviderId::new(record.integration_provider.clone())?,
            environment: record.environment.clone(),
            segments: record
                .segments
                .iter()
                .cloned()
                .map(SegmentKey::new)
                .collect::<Result<_, _>>()?,
            products: record
                .segments
                .iter()
                .map(|segment| {
                    record
                        .product_for_segment(segment)
                        .unwrap_or(segment)
                        .to_owned()
                })
                .collect(),
            account_model: record.account_model.clone(),
            credential_id: record.credential_id.clone(),
            configured_credential_role: configured_credential_role(record),
            capabilities: account_capabilities(record),
            status: record.status.clone(),
        })
    }
}

fn configured_credential_role(record: &AccountBindingRecord) -> String {
    record
        .credential_role
        .clone()
        .or_else(|| {
            record
                .credentials
                .first()
                .map(|binding| binding.role.clone())
        })
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| "readonly".into())
}

fn account_capabilities(record: &AccountBindingRecord) -> Vec<String> {
    let mut capabilities = vec!["read".to_owned()];
    let roles = record.credential_role.iter().map(String::as_str).chain(
        record
            .credentials
            .iter()
            .map(|binding| binding.role.as_str()),
    );
    for role in roles {
        match role.trim().to_ascii_lowercase().as_str() {
            "trade" | "trading" => {
                if !capabilities.iter().any(|value| value == "trade") {
                    capabilities.push("trade".into());
                }
            },
            "transfer" | "admin" => {
                for capability in ["trade", "transfer"] {
                    if !capabilities.iter().any(|value| value == capability) {
                        capabilities.push(capability.into());
                    }
                }
            },
            _ => {},
        }
    }
    capabilities.retain(|capability| {
        record.permissions.get(capability).is_none_or(|permission| {
            matches!(
                permission.trim().to_ascii_lowercase().as_str(),
                "granted" | "true" | "enabled" | "allowed"
            )
        })
    });
    capabilities
}

impl CliAccountApplication {
    pub fn open(workspace: &Workspace) -> Result<Self, Box<dyn std::error::Error>> {
        let registry_path = workspace.child(&["config", "accounts", "accounts.toml"])?;
        let registry_read_path = workspace.existing_path(
            &["config", "accounts", "accounts.toml"],
            &["accounts", "accounts.toml"],
        )?;
        let mut registry = AccountRegistry::load(&registry_read_path)?;
        let credentials_path = workspace.child(&["config", "credentials", "credentials.toml"])?;
        let credentials_read_path = workspace.existing_path(
            &["config", "credentials", "credentials.toml"],
            &["credentials", "credentials.toml"],
        )?;
        let credential_store = CredentialStore::load(&credentials_read_path)?;
        for account in &mut registry.accounts {
            if account.credentials.is_empty() {
                if let Some(credential_id) = account.credential_id.clone() {
                    account.credentials.push(AccountCredentialBinding {
                        name: "default".into(),
                        credential_id,
                        role: account
                            .credential_role
                            .clone()
                            .unwrap_or_else(|| "readonly".into()),
                    });
                }
            }
        }
        Ok(Self {
            registry_path,
            registry,
            credentials_path,
            credential_store,
            provider_quota_ledger_path: workspace
                .state_root()
                .join("integration")
                .join("provider-quota.mmap"),
            reference_database: workspace.child(&["state", "reference", "reference.sqlite"])?,
        })
    }

    pub fn list_accounts(&self) -> Result<AccountListResult, Box<dyn std::error::Error>> {
        let accounts = self
            .registry
            .accounts
            .iter()
            .map(AccountListItem::try_from)
            .collect::<Result<Vec<_>, _>>()?;
        let count = u64::try_from(accounts.len())?;
        Ok(AccountListResult { accounts, count })
    }

    pub fn trading_binding(
        &self,
        account_id: &str,
        segment: Option<&str>,
        access: &str,
    ) -> Result<AccountTradingBinding, Box<dyn std::error::Error>> {
        let account = self.account(account_id)?;
        let segment = match segment {
            Some(value) if account.segments.iter().any(|item| item == value) => value,
            Some(value) => {
                return Err(
                    format!("account {account_id} does not provide segment {value}").into(),
                );
            },
            None if account.segments.len() == 1 => account.segments[0].as_str(),
            None if account.segments.is_empty() => {
                return Err(format!("account {account_id} has no trading segment").into());
            },
            None => {
                return Err(format!(
                    "account {account_id} has multiple trading segments; --segment is required"
                )
                .into());
            },
        };
        let require_trade = matches!(access, "write" | "trade");
        if !matches!(access, "read" | "write" | "trade") {
            return Err(format!("unsupported trading binding access: {access}").into());
        }
        let selected = account.credentials.iter().find(|binding| {
            let role = binding.role.trim().to_ascii_lowercase();
            if require_trade {
                matches!(role.as_str(), "trade" | "trading" | "transfer" | "admin")
            } else {
                matches!(
                    role.as_str(),
                    "readonly" | "read" | "trade" | "trading" | "transfer" | "admin"
                )
            }
        });
        let credential_id = selected
            .map(|value| value.credential_id.clone())
            .or_else(|| account.credential_id.clone());
        let credential_role = selected
            .map(|value| value.role.clone())
            .or_else(|| account.credential_role.clone())
            .unwrap_or_else(|| "readonly".into());
        if require_trade
            && !matches!(
                credential_role.trim().to_ascii_lowercase().as_str(),
                "trade" | "trading" | "transfer" | "admin"
            )
        {
            return Err(
                format!("account {account_id} has no credential with trade permission").into(),
            );
        }
        let provider = account.integration_provider.trim().to_ascii_lowercase();
        if !matches!(provider.as_str(), "ibkr" | "paper" | "simulated") && credential_id.is_none() {
            return Err(format!("account {account_id} has no credential binding").into());
        }
        let provider_product = account
            .product_for_segment(segment)
            .unwrap_or(segment)
            .to_owned();
        let base_url = account
            .values
            .get("base_url")
            .cloned()
            .filter(|value| !value.trim().is_empty())
            .unwrap_or_else(|| {
                default_rest_endpoint(&provider, &provider_product)
                    .unwrap_or_default()
                    .to_owned()
            });
        Ok(AccountTradingBinding {
            account_id: AccountId::new(account.account_id.clone())?,
            remote_account_id: account
                .remote_identity
                .clone()
                .unwrap_or_else(|| account.account_id.clone()),
            provider: ProviderId::new(provider)?,
            environment: account.environment.clone(),
            segment_key: SegmentKey::new(segment.to_owned())?,
            provider_product,
            trading_mode: account.segment_trading_modes.get(segment).cloned(),
            credential_id,
            credential_role,
            base_url,
            host: account
                .values
                .get("host")
                .cloned()
                .unwrap_or_else(|| "127.0.0.1".into()),
            port: account
                .values
                .get("port")
                .and_then(|value| value.parse().ok())
                .unwrap_or(4002),
            client_id: account
                .values
                .get("client_id")
                .and_then(|value| value.parse().ok())
                .unwrap_or_default(),
            isolated_symbol: account.values.get("isolated_symbol").cloned(),
        })
    }

    pub fn browse_accounts(
        &self,
        query: Option<&str>,
    ) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
        let query = query.map(str::to_ascii_lowercase);
        let accounts: Vec<_> = self
            .registry
            .accounts
            .iter()
            .filter(|record| {
                query.as_deref().is_none_or(|query| {
                    record.account_id.to_ascii_lowercase().contains(query)
                        || record.alias.to_ascii_lowercase().contains(query)
                        || record.broker.to_ascii_lowercase().contains(query)
                        || record
                            .segments
                            .iter()
                            .any(|segment| segment.to_ascii_lowercase().contains(query))
                })
            })
            .cloned()
            .collect();
        Ok(serde_json::json!({
            "accounts": accounts,
            "count": accounts.len(),
        }))
    }

    pub fn show_account(
        &self,
        account_id: &str,
    ) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
        let value = self
            .registry
            .accounts
            .iter()
            .find(|record| record.account_id == account_id)
            .ok_or_else(|| format!("account not found: {account_id}"))?;
        Ok(serde_json::to_value(value)?)
    }

    pub async fn overview(
        &self,
        account_id: &str,
    ) -> Result<AccountOverviewResult, Box<dyn std::error::Error>> {
        let account = self.account(account_id)?;
        let identity = AccountOverviewIdentity {
            account_id: AccountId::new(account.account_id.clone())?,
            alias: account.alias.clone(),
            broker: BrokerId::new(account.broker.clone())?,
            exchange: account.exchange.clone(),
            environment: account.environment.clone(),
            masked_remote_identity: account.remote_identity.as_deref().map(mask_identity),
        };
        let connection = AccountOverviewConnection {
            integration_provider: ProviderId::new(account.integration_provider.clone())?,
            credential_bindings: account
                .credentials
                .iter()
                .map(|binding| AccountCredentialSummary {
                    name: binding.name.clone(),
                    credential_id: binding.credential_id.clone(),
                    role: binding.role.clone(),
                })
                .collect(),
        };
        let permissions = AccountOverviewPermissions {
            configured_credential_role: configured_credential_role(account),
            observed_permissions: account.permissions.clone(),
            effective_capabilities: account_capabilities(account),
        };
        let selected_segments = selected_segments(account, &[])?;
        let segments_requested = u64::try_from(selected_segments.len())?;
        if is_paper_or_simulated(&account.broker)
            || is_paper_or_simulated(&account.integration_provider)
            || is_paper_or_simulated(&account.environment)
        {
            let balances = self.local_balances_for(account)?;
            let segment_profiles = selected_segments
                .iter()
                .map(|segment| AccountSegmentProfileItem {
                    segment: SegmentKey::new(segment.clone()).expect("selected segment is valid"),
                    source: "local_registry".into(),
                    freshness: "local".into(),
                    completeness: AccountQueryCompleteness::Complete,
                    observed_at_unix_nanos: None,
                    issue: None,
                    configured_account_model: account.account_model.clone(),
                    observed_account_model: account.account_model.clone(),
                    provider_account_model: None,
                    margin_mode: None,
                    position_mode: None,
                })
                .collect();
            return Ok(AccountOverviewResult {
                identity,
                connection,
                profile: overview_profile(
                    account.account_model.clone(),
                    segment_profiles,
                    None,
                    None,
                ),
                permissions,
                commercial: AccountOverviewCommercial {
                    vip_tier: None,
                    bnb_fee_discount: None,
                    fee_summary_status: if account.fee_rate.is_some() {
                        "simulated_configured"
                    } else {
                        "not_configured"
                    }
                    .into(),
                },
                facts: AccountOverviewFacts {
                    non_zero_balance_count: u64::try_from(
                        balances
                            .iter()
                            .filter(|balance| balance.total.mantissa() != 0)
                            .count(),
                    )?,
                    collateral_count: 0,
                    position_count: 0,
                    earn_holding_count: Some(0),
                    open_order_count: Some(0),
                },
                health: AccountOverviewHealth {
                    source: "local_registry".into(),
                    mode: "standalone".into(),
                    overall_status: "configured".into(),
                    freshness: "local".into(),
                    completeness: AccountQueryCompleteness::Complete,
                    segments_requested,
                    segments_succeeded: segments_requested,
                    observed_at_unix_nanos: None,
                    issues: Vec::new(),
                },
            });
        }

        let mut base_options = self.direct_query_options(account)?;
        let mut non_zero_balance_count = 0_u64;
        let mut collateral_count = 0_u64;
        let mut position_count = 0_u64;
        let mut segments_succeeded = 0_u64;
        let mut observed_at_unix_nanos: Option<u64> = None;
        let mut issues = Vec::new();
        let mut segment_profiles = Vec::new();
        let account_info = if account.integration_provider.eq_ignore_ascii_case("binance") {
            match query_direct_account_info(&base_options).await {
                Ok(info) => Some(info),
                Err(message) => {
                    issues.push(AccountQueryError {
                        segment: SegmentKey::new("account")?,
                        message: format!("VIP 等级查询失败：{message}"),
                    });
                    None
                },
            }
        } else {
            None
        };
        let explicit_profile = if account.integration_provider.eq_ignore_ascii_case("binance") {
            match account_info
                .as_ref()
                .and_then(|info| info.portfolio_margin_enabled)
            {
                Some(true) => {
                    let profile = crate::composition::account::ObservedAccountProfile {
                        account_model: "portfolio_margin".into(),
                        provider_account_model: "portfolio_margin".into(),
                    };
                    base_options.account_model = Some(profile.provider_account_model.clone());
                    Some(profile)
                },
                Some(false) => None,
                None => match query_direct_account_profile(&base_options).await {
                    Ok(profile) => {
                        base_options.account_model = Some(profile.provider_account_model.clone());
                        Some(profile)
                    },
                    Err(message) => {
                        issues.push(AccountQueryError {
                            segment: SegmentKey::new("account")?,
                            message: format!("统一账户探测失败：{message}"),
                        });
                        None
                    },
                },
            }
        } else {
            None
        };
        for segment in &selected_segments {
            let product = account.product_for_segment(segment).unwrap_or(segment);
            let binding = AccountSegmentBinding {
                segment_key: segment.clone(),
                provider_product: product.to_owned(),
                trading_mode: account.segment_trading_modes.get(segment).cloned(),
            };
            let mut options = base_options.clone();
            options.product = product.to_owned();
            if account.values.get("base_url").is_none() {
                options.base_url = default_rest_endpoint(&options.provider, product)?.to_owned();
            }
            match query_direct_account_snapshot(&options, &binding).await {
                Ok(snapshot) => {
                    segments_succeeded = segments_succeeded.saturating_add(1);
                    non_zero_balance_count = non_zero_balance_count.saturating_add(u64::try_from(
                        snapshot
                            .balances
                            .iter()
                            .filter(|balance| balance.total.mantissa != 0)
                            .count(),
                    )?);
                    collateral_count = collateral_count.saturating_add(u64::try_from(
                        snapshot
                            .collateral
                            .iter()
                            .filter(|balance| balance.total.mantissa != 0)
                            .count(),
                    )?);
                    position_count =
                        position_count.saturating_add(u64::try_from(snapshot.positions.len())?);
                    observed_at_unix_nanos = Some(
                        observed_at_unix_nanos
                            .unwrap_or_default()
                            .max(snapshot.observed_at_unix_nanos.get()),
                    );
                    let position_mode = if let Some(value) = snapshot.position_mode {
                        Some(value)
                    } else if matches!(
                        product,
                        "usd_m_futures" | "usd-m-futures" | "coin_m_futures" | "coin-m-futures"
                    ) {
                        match query_direct_position_mode(&options, product).await {
                            Ok(value) => Some(value),
                            Err(message) => {
                                issues.push(AccountQueryError {
                                    segment: snapshot.segment_key.clone(),
                                    message: format!("持仓模式查询失败：{message}"),
                                });
                                None
                            },
                        }
                    } else {
                        None
                    };
                    segment_profiles.push(AccountSegmentProfileItem {
                        segment: snapshot.segment_key,
                        source: "direct_provider".into(),
                        freshness: "fresh".into(),
                        completeness: AccountQueryCompleteness::Complete,
                        observed_at_unix_nanos: Some(snapshot.observed_at_unix_nanos.get()),
                        issue: None,
                        configured_account_model: account.account_model.clone(),
                        observed_account_model: snapshot.account_model.map(account_model_name),
                        provider_account_model: snapshot.provider_account_model,
                        margin_mode: snapshot.margin_mode.map(|value| {
                            match value {
                                kairos_conflux::ExternalMarginMode::Cross => "cross",
                                kairos_conflux::ExternalMarginMode::Isolated => "isolated",
                            }
                            .into()
                        }),
                        position_mode: position_mode.map(|value| {
                            match value {
                                kairos_conflux::ExternalPositionMode::OneWay => "one_way",
                                kairos_conflux::ExternalPositionMode::Hedge => "hedge",
                            }
                            .into()
                        }),
                    });
                },
                Err(message) => {
                    let segment = SegmentKey::new(segment.clone())?;
                    segment_profiles.push(AccountSegmentProfileItem {
                        segment: segment.clone(),
                        source: "direct_provider".into(),
                        freshness: "unknown".into(),
                        completeness: classify_query_failure(&message),
                        observed_at_unix_nanos: None,
                        issue: Some(message.clone()),
                        configured_account_model: account.account_model.clone(),
                        observed_account_model: None,
                        provider_account_model: None,
                        margin_mode: None,
                        position_mode: None,
                    });
                    issues.push(AccountQueryError { segment, message });
                },
            }
        }
        let mut completeness = query_completeness(segments_succeeded, segments_requested);
        let earn_holding_count = if account.integration_provider.eq_ignore_ascii_case("binance")
            && account.segments.iter().any(|segment| {
                account
                    .product_for_segment(segment)
                    .is_some_and(|product| product.eq_ignore_ascii_case("funding"))
            }) {
            match query_direct_earn_positions(&base_options).await {
                Ok(positions) => Some(u64::try_from(positions.len())?),
                Err(message) => {
                    let segment = account
                        .segments
                        .iter()
                        .find(|segment| {
                            account
                                .product_for_segment(segment)
                                .is_some_and(|product| product.eq_ignore_ascii_case("funding"))
                        })
                        .expect("Binance funding segment exists");
                    issues.push(AccountQueryError {
                        segment: SegmentKey::new(segment.clone())?,
                        message: format!("Earn holdings: {message}"),
                    });
                    if completeness == AccountQueryCompleteness::Complete {
                        completeness = AccountQueryCompleteness::Partial;
                    }
                    None
                },
            }
        } else {
            Some(0)
        };
        let mut open_order_count = 0_u64;
        let mut open_orders_complete = true;
        for segment in &selected_segments {
            let product = account.product_for_segment(segment).unwrap_or(segment);
            if product.eq_ignore_ascii_case("funding") {
                continue;
            }
            let binding = AccountSegmentBinding {
                segment_key: segment.clone(),
                provider_product: product.to_owned(),
                trading_mode: account.segment_trading_modes.get(segment).cloned(),
            };
            let mut options = base_options.clone();
            options.product = product.to_owned();
            if account.values.get("base_url").is_none() {
                options.base_url = default_rest_endpoint(&options.provider, product)?.to_owned();
            }
            match query_direct_open_orders(&options, &binding, None).await {
                Ok(orders) => {
                    open_order_count =
                        open_order_count.saturating_add(u64::try_from(orders.len())?);
                },
                Err(message) => {
                    open_orders_complete = false;
                    issues.push(AccountQueryError {
                        segment: SegmentKey::new(segment.clone())?,
                        message: format!("未完成订单查询失败：{message}"),
                    });
                },
            }
        }
        if !open_orders_complete && completeness == AccountQueryCompleteness::Complete {
            completeness = AccountQueryCompleteness::Partial;
        }
        Ok(AccountOverviewResult {
            identity,
            connection,
            profile: overview_profile(
                account.account_model.clone(),
                segment_profiles,
                explicit_profile,
                account_info
                    .as_ref()
                    .and_then(|info| info.portfolio_margin_enabled),
            ),
            permissions,
            commercial: AccountOverviewCommercial {
                vip_tier: account_info.map(|info| format!("VIP {}", info.vip_level)),
                bnb_fee_discount: None,
                fee_summary_status: if account.fee_rate.is_some() {
                    "registry_value_deprecated_not_observed"
                } else {
                    "query_by_symbol"
                }
                .into(),
            },
            facts: AccountOverviewFacts {
                non_zero_balance_count,
                collateral_count,
                position_count,
                earn_holding_count,
                open_order_count: open_orders_complete.then_some(open_order_count),
            },
            health: AccountOverviewHealth {
                source: "direct_provider".into(),
                mode: "standalone".into(),
                overall_status: match completeness {
                    AccountQueryCompleteness::Complete => "ready",
                    AccountQueryCompleteness::Partial => "degraded",
                    _ => "unavailable",
                }
                .into(),
                freshness: if observed_at_unix_nanos.is_some() {
                    "fresh"
                } else {
                    "unknown"
                }
                .into(),
                completeness,
                segments_requested,
                segments_succeeded,
                observed_at_unix_nanos,
                issues,
            },
        })
    }

    pub fn local_snapshot(
        &self,
        account_id: &str,
    ) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
        let account = self.local_query_account(account_id)?;
        Ok(serde_json::json!({
            "account_id": account.account_id,
            "source": "local_registry",
            "mode": "standalone",
            "kind": "snapshot",
            "segments": self.local_segments(account)?,
            "positions": [],
            "open_orders": [],
        }))
    }

    pub async fn balances(
        &self,
        account_id: &str,
        segments: &[String],
        include_zero: bool,
    ) -> Result<AccountBalancesResult, Box<dyn std::error::Error>> {
        self.query_assets(account_id, segments, include_zero, false)
            .await
    }

    pub async fn assets(
        &self,
        account_id: &str,
        segments: &[String],
        include_zero: bool,
    ) -> Result<AccountBalancesResult, Box<dyn std::error::Error>> {
        self.query_assets(account_id, segments, include_zero, true)
            .await
    }

    async fn query_assets(
        &self,
        account_id: &str,
        segments: &[String],
        include_zero: bool,
        include_collateral: bool,
    ) -> Result<AccountBalancesResult, Box<dyn std::error::Error>> {
        let account = self.account(account_id)?;
        if is_paper_or_simulated(&account.broker)
            || is_paper_or_simulated(&account.integration_provider)
            || is_paper_or_simulated(&account.environment)
        {
            let selected_segments = selected_segments(account, segments)?;
            let balances = self
                .local_balances_for(account)?
                .into_iter()
                .filter(|value| selected_segments.contains(&value.segment.to_string()))
                .filter(|value| include_zero || value.total.mantissa() != 0)
                .collect();
            let segment_count = u64::try_from(selected_segments.len())?;
            return Ok(AccountBalancesResult {
                account_id: AccountId::new(account.account_id.clone())?,
                source: "local_registry".into(),
                mode: "standalone".into(),
                kind: if include_collateral {
                    "assets"
                } else {
                    "balances"
                }
                .into(),
                segments_requested: segment_count,
                segments_succeeded: segment_count,
                completeness: AccountQueryCompleteness::Complete,
                observed_at_unix_nanos: Some(now_unix_nanos()),
                balances,
                collateral: Vec::new(),
                outcomes: selected_segments
                    .into_iter()
                    .map(|segment| {
                        Ok(AccountSegmentQueryOutcome {
                            segment: SegmentKey::new(segment)?,
                            outcome: AccountQueryCompleteness::Complete,
                            message: None,
                        })
                    })
                    .collect::<Result<Vec<_>, kairos_primitives::DomainTypeError>>()?,
                errors: Vec::new(),
            });
        }

        let base_options = self.direct_query_options(account)?;
        let mut balances = Vec::new();
        let mut collateral = Vec::new();
        let mut errors = Vec::new();
        let mut outcomes = Vec::new();
        let mut observed_at_unix_nanos: Option<u64> = None;
        let selected_segments = selected_segments(account, segments)?;
        let segments_requested = u64::try_from(selected_segments.len())?;
        let mut segments_succeeded = 0_u64;
        for segment in &selected_segments {
            let product = account.product_for_segment(segment).unwrap_or(segment);
            let binding = AccountSegmentBinding {
                segment_key: segment.clone(),
                provider_product: product.to_owned(),
                trading_mode: account.segment_trading_modes.get(segment).cloned(),
            };
            let mut options = base_options.clone();
            options.product = product.to_owned();
            if account.values.get("base_url").is_none() {
                options.base_url = default_rest_endpoint(&options.provider, product)?.to_owned();
            }
            match query_direct_account_snapshot(&options, &binding).await {
                Ok(snapshot) => {
                    segments_succeeded = segments_succeeded.saturating_add(1);
                    observed_at_unix_nanos = Some(
                        observed_at_unix_nanos
                            .unwrap_or_default()
                            .max(snapshot.observed_at_unix_nanos.get()),
                    );
                    outcomes.push(AccountSegmentQueryOutcome {
                        segment: snapshot.segment_key.clone(),
                        outcome: AccountQueryCompleteness::Complete,
                        message: None,
                    });
                    append_external_balances(
                        &mut balances,
                        snapshot.segment_key.clone(),
                        "wallet",
                        snapshot.balances,
                        include_zero,
                    )?;
                    if include_collateral {
                        append_external_balances(
                            &mut collateral,
                            snapshot.segment_key,
                            "collateral",
                            snapshot.collateral,
                            include_zero,
                        )?;
                    }
                },
                Err(message) => {
                    let segment = SegmentKey::new(segment.clone())?;
                    outcomes.push(AccountSegmentQueryOutcome {
                        segment: segment.clone(),
                        outcome: classify_query_failure(&message),
                        message: Some(message.clone()),
                    });
                    errors.push(AccountQueryError { segment, message });
                },
            }
        }
        Ok(AccountBalancesResult {
            account_id: AccountId::new(account.account_id.clone())?,
            source: "direct_provider".into(),
            mode: "standalone".into(),
            kind: if include_collateral {
                "assets"
            } else {
                "balances"
            }
            .into(),
            segments_requested,
            segments_succeeded,
            completeness: aggregate_outcomes(segments_succeeded, &outcomes),
            observed_at_unix_nanos,
            balances,
            collateral,
            outcomes,
            errors,
        })
    }

    pub async fn positions(
        &self,
        account_id: &str,
        segments: &[String],
        symbol: Option<&str>,
    ) -> Result<AccountPositionsResult, Box<dyn std::error::Error>> {
        let account = self.account(account_id)?;
        let selected_segments = selected_segments(account, segments)?;
        let segments_requested = u64::try_from(selected_segments.len())?;
        if is_paper_or_simulated(&account.broker)
            || is_paper_or_simulated(&account.integration_provider)
            || is_paper_or_simulated(&account.environment)
        {
            return Ok(AccountPositionsResult {
                account_id: AccountId::new(account.account_id.clone())?,
                source: "local_registry".into(),
                mode: "standalone".into(),
                kind: "positions".into(),
                segments_requested,
                segments_succeeded: segments_requested,
                completeness: AccountQueryCompleteness::Complete,
                observed_at_unix_nanos: Some(now_unix_nanos()),
                positions: Vec::new(),
                outcomes: selected_segments
                    .into_iter()
                    .map(|segment| {
                        Ok(AccountSegmentQueryOutcome {
                            segment: SegmentKey::new(segment)?,
                            outcome: AccountQueryCompleteness::Complete,
                            message: None,
                        })
                    })
                    .collect::<Result<Vec<_>, kairos_primitives::DomainTypeError>>()?,
                errors: Vec::new(),
            });
        }

        let base_options = self.direct_query_options(account)?;
        let mut positions = Vec::new();
        let mut errors = Vec::new();
        let mut outcomes = Vec::new();
        let mut observed_at_unix_nanos: Option<u64> = None;
        let mut segments_succeeded = 0_u64;
        for segment in &selected_segments {
            let product = account.product_for_segment(segment).unwrap_or(segment);
            let binding = AccountSegmentBinding {
                segment_key: segment.clone(),
                provider_product: product.to_owned(),
                trading_mode: account.segment_trading_modes.get(segment).cloned(),
            };
            let mut options = base_options.clone();
            options.product = product.to_owned();
            if account.values.get("base_url").is_none() {
                options.base_url = default_rest_endpoint(&options.provider, product)?.to_owned();
            }
            match query_direct_account_snapshot(&options, &binding).await {
                Ok(snapshot) => {
                    segments_succeeded = segments_succeeded.saturating_add(1);
                    observed_at_unix_nanos = Some(
                        observed_at_unix_nanos
                            .unwrap_or_default()
                            .max(snapshot.observed_at_unix_nanos.get()),
                    );
                    outcomes.push(AccountSegmentQueryOutcome {
                        segment: snapshot.segment_key.clone(),
                        outcome: AccountQueryCompleteness::Complete,
                        message: None,
                    });
                    let margin_mode = snapshot.margin_mode.map(|value| match value {
                        kairos_conflux::ExternalMarginMode::Cross => "cross".into(),
                        kairos_conflux::ExternalMarginMode::Isolated => "isolated".into(),
                    });
                    let observed_position_mode = if let Some(value) = snapshot.position_mode {
                        Some(value)
                    } else if matches!(
                        product,
                        "usd_m_futures" | "usd-m-futures" | "coin_m_futures" | "coin-m-futures"
                    ) {
                        query_direct_position_mode(&options, product).await.ok()
                    } else {
                        None
                    };
                    let position_mode = observed_position_mode.map(|value| match value {
                        kairos_conflux::ExternalPositionMode::OneWay => "one_way".into(),
                        kairos_conflux::ExternalPositionMode::Hedge => "hedge".into(),
                    });
                    for position in snapshot.positions {
                        let position_symbol =
                            position.participant_instrument.source_symbol.to_string();
                        if symbol.is_some_and(|value| !position_symbol.eq_ignore_ascii_case(value))
                        {
                            continue;
                        }
                        positions.push(AccountPositionItem {
                            segment: snapshot.segment_key.clone(),
                            symbol: position_symbol,
                            instrument_type: position
                                .participant_instrument
                                .instrument_type
                                .map(|value| value.as_str().to_owned()),
                            side: position.position_side.as_str().into(),
                            quantity: decimal_parts(position.quantity)?,
                            average_price: position.average_price.map(decimal_parts).transpose()?,
                            mark_price: position.mark_price.map(decimal_parts).transpose()?,
                            unrealized_pnl: position
                                .unrealized_pnl
                                .map(decimal_parts)
                                .transpose()?,
                            realized_pnl: position.realized_pnl.map(decimal_parts).transpose()?,
                            margin_mode: margin_mode.clone(),
                            position_mode: position_mode.clone(),
                        });
                    }
                },
                Err(message) => {
                    let segment = SegmentKey::new(segment.clone())?;
                    outcomes.push(AccountSegmentQueryOutcome {
                        segment: segment.clone(),
                        outcome: classify_query_failure(&message),
                        message: Some(message.clone()),
                    });
                    errors.push(AccountQueryError { segment, message });
                },
            }
        }
        Ok(AccountPositionsResult {
            account_id: AccountId::new(account.account_id.clone())?,
            source: "direct_provider".into(),
            mode: "standalone".into(),
            kind: "positions".into(),
            segments_requested,
            segments_succeeded,
            completeness: aggregate_outcomes(segments_succeeded, &outcomes),
            observed_at_unix_nanos,
            positions,
            outcomes,
            errors,
        })
    }

    pub async fn earn_holdings(
        &self,
        account_id: &str,
        family_filter: Option<&str>,
        asset_filter: Option<&str>,
    ) -> Result<AccountEarnHoldingsResult, Box<dyn std::error::Error>> {
        let account = self.account(account_id)?;
        let segment = account
            .segments
            .iter()
            .find(|segment| {
                account
                    .product_for_segment(segment)
                    .is_some_and(|product| product.eq_ignore_ascii_case("funding"))
            })
            .or_else(|| account.segments.first())
            .ok_or("account has no segment for Earn holdings")?;
        let segment_key = SegmentKey::new(segment.clone())?;
        if is_paper_or_simulated(&account.broker)
            || is_paper_or_simulated(&account.integration_provider)
            || is_paper_or_simulated(&account.environment)
        {
            return Ok(AccountEarnHoldingsResult {
                account_id: AccountId::new(account.account_id.clone())?,
                source: "local_registry".into(),
                mode: "standalone".into(),
                kind: "earn_holdings".into(),
                completeness: AccountQueryCompleteness::Complete,
                observed_at_unix_nanos: Some(now_unix_nanos()),
                holdings: Vec::new(),
                outcomes: vec![AccountSegmentQueryOutcome {
                    segment: segment_key,
                    outcome: AccountQueryCompleteness::Complete,
                    message: None,
                }],
                errors: Vec::new(),
            });
        }
        let options = self.direct_query_options(account)?;
        match query_direct_earn_positions(&options).await {
            Ok(positions) => {
                let holdings: Vec<AccountEarnHoldingItem> = positions
                    .into_iter()
                    .map(|position| map_earn_holding(segment_key.clone(), position))
                    .collect::<Result<Vec<_>, _>>()?
                    .into_iter()
                    .filter(|holding| {
                        family_filter
                            .is_none_or(|family| holding.family.eq_ignore_ascii_case(family))
                            && asset_filter.is_none_or(|asset| {
                                holding.asset.as_str().eq_ignore_ascii_case(asset)
                            })
                    })
                    .collect();
                Ok(AccountEarnHoldingsResult {
                    account_id: AccountId::new(account.account_id.clone())?,
                    source: "direct_provider".into(),
                    mode: "standalone".into(),
                    kind: "earn_holdings".into(),
                    completeness: AccountQueryCompleteness::Complete,
                    observed_at_unix_nanos: holdings
                        .iter()
                        .filter_map(|holding| holding.observed_at_unix_nanos)
                        .max()
                        .or_else(|| Some(now_unix_nanos())),
                    holdings,
                    outcomes: vec![AccountSegmentQueryOutcome {
                        segment: segment_key,
                        outcome: AccountQueryCompleteness::Complete,
                        message: None,
                    }],
                    errors: Vec::new(),
                })
            },
            Err(message) => {
                let outcome = classify_query_failure(&message);
                Ok(AccountEarnHoldingsResult {
                    account_id: AccountId::new(account.account_id.clone())?,
                    source: "direct_provider".into(),
                    mode: "standalone".into(),
                    kind: "earn_holdings".into(),
                    completeness: outcome.clone(),
                    observed_at_unix_nanos: Some(now_unix_nanos()),
                    holdings: Vec::new(),
                    outcomes: vec![AccountSegmentQueryOutcome {
                        segment: segment_key.clone(),
                        outcome,
                        message: Some(message.clone()),
                    }],
                    errors: vec![AccountQueryError {
                        segment: segment_key,
                        message,
                    }],
                })
            },
        }
    }

    pub async fn open_orders(
        &self,
        account_id: &str,
        segments: &[String],
        symbol: Option<&str>,
    ) -> Result<AccountOpenOrdersResult, Box<dyn std::error::Error>> {
        let account = self.account(account_id)?;
        let selected_segments = selected_segments(account, segments)?;
        let segments_requested = u64::try_from(selected_segments.len())?;
        if is_paper_or_simulated(&account.broker)
            || is_paper_or_simulated(&account.integration_provider)
            || is_paper_or_simulated(&account.environment)
        {
            return Ok(AccountOpenOrdersResult {
                account_id: AccountId::new(account.account_id.clone())?,
                source: "local_registry".into(),
                mode: "standalone".into(),
                kind: "open_orders".into(),
                completeness: AccountQueryCompleteness::Complete,
                segments_requested,
                segments_succeeded: segments_requested,
                observed_at_unix_nanos: Some(now_unix_nanos()),
                orders: Vec::new(),
                outcomes: selected_segments
                    .into_iter()
                    .map(|segment| {
                        Ok(AccountSegmentQueryOutcome {
                            segment: SegmentKey::new(segment)?,
                            outcome: AccountQueryCompleteness::Complete,
                            message: None,
                        })
                    })
                    .collect::<Result<Vec<_>, kairos_primitives::DomainTypeError>>()?,
            });
        }

        let base_options = self.direct_query_options(account)?;
        let mut orders = Vec::new();
        let mut outcomes = Vec::new();
        let mut segments_succeeded = 0_u64;
        for segment in &selected_segments {
            let product = account.product_for_segment(segment).unwrap_or(segment);
            let binding = AccountSegmentBinding {
                segment_key: segment.clone(),
                provider_product: product.to_owned(),
                trading_mode: account.segment_trading_modes.get(segment).cloned(),
            };
            let mut options = base_options.clone();
            options.product = product.to_owned();
            if account.values.get("base_url").is_none() {
                options.base_url = default_rest_endpoint(&options.provider, product)?.to_owned();
            }
            let segment_key = SegmentKey::new(segment.clone())?;
            match query_direct_open_orders(&options, &binding, symbol).await {
                Ok(external_orders) => {
                    segments_succeeded = segments_succeeded.saturating_add(1);
                    orders.extend(
                        external_orders
                            .into_iter()
                            .map(|order| map_open_order(segment_key.clone(), order))
                            .collect::<Result<Vec<_>, _>>()?,
                    );
                    outcomes.push(AccountSegmentQueryOutcome {
                        segment: segment_key,
                        outcome: AccountQueryCompleteness::Complete,
                        message: None,
                    });
                },
                Err(message) => outcomes.push(AccountSegmentQueryOutcome {
                    segment: segment_key,
                    outcome: classify_query_failure(&message),
                    message: Some(message),
                }),
            }
        }
        let completeness = aggregate_outcomes(segments_succeeded, &outcomes);
        Ok(AccountOpenOrdersResult {
            account_id: AccountId::new(account.account_id.clone())?,
            source: "direct_provider".into(),
            mode: "standalone".into(),
            kind: "open_orders".into(),
            completeness,
            segments_requested,
            segments_succeeded,
            observed_at_unix_nanos: Some(now_unix_nanos()),
            orders,
            outcomes,
        })
    }

    pub async fn fees(
        &self,
        account_id: &str,
        requested_product: &str,
        symbol: Option<&str>,
    ) -> Result<AccountFeesResult, Box<dyn std::error::Error>> {
        let account = self.account(account_id)?;
        let product = resolve_fee_product(account, requested_product)?;
        if is_paper_or_simulated(&account.broker)
            || is_paper_or_simulated(&account.integration_provider)
            || is_paper_or_simulated(&account.environment)
        {
            let rate = account
                .fee_rate
                .as_deref()
                .map(str::parse::<DecimalParts>)
                .transpose()?;
            return Ok(AccountFeesResult {
                account_id: AccountId::new(account.account_id.clone())?,
                source: "local_registry".into(),
                mode: "standalone".into(),
                kind: "fees".into(),
                product,
                symbol: symbol.map(str::to_owned),
                completeness: if rate.is_some() {
                    AccountQueryCompleteness::Complete
                } else {
                    AccountQueryCompleteness::Unavailable
                },
                maker: rate,
                taker: rate,
                buyer: None,
                seller: None,
                standard: None,
                special: None,
                tax: None,
                discount: None,
                rpi: None,
                vip_tier: None,
                vip_tier_status: "not_applicable".into(),
                observed_at_unix_nanos: Some(now_unix_nanos()),
                issues: if rate.is_some() {
                    Vec::new()
                } else {
                    vec!["paper account has no simulated fee_rate configured".into()]
                },
            });
        }
        let Some(symbol) = symbol else {
            return Ok(AccountFeesResult {
                account_id: AccountId::new(account.account_id.clone())?,
                source: "not_queried".into(),
                mode: "standalone".into(),
                kind: "fees".into(),
                product,
                symbol: None,
                completeness: AccountQueryCompleteness::Unsupported,
                maker: None,
                taker: None,
                buyer: None,
                seller: None,
                standard: None,
                special: None,
                tax: None,
                discount: None,
                rpi: None,
                vip_tier: None,
                vip_tier_status: "not_queried".into(),
                observed_at_unix_nanos: None,
                issues: vec![format!(
                    "{requested_product} fee schedule requires --symbol"
                )],
            });
        };
        let mut options = self.direct_query_options(account)?;
        options.product = product.clone();
        if account.values.get("base_url").is_none() {
            options.base_url = default_rest_endpoint(&options.provider, &product)?.to_owned();
        }
        let account_info = query_direct_account_info(&options).await;
        match query_direct_fee_schedule(&options, &product, symbol).await {
            Ok(schedule) => {
                let (vip_level, account_info_issue) = match account_info {
                    Ok(info) => (Some(info.vip_level), None),
                    Err(message) => (None, Some(format!("VIP 等级查询失败：{message}"))),
                };
                let mut result = map_fee_schedule(account, product, schedule, vip_level)?;
                if let Some(issue) = account_info_issue {
                    result.issues.push(issue);
                }
                Ok(result)
            },
            Err(message) => Ok(AccountFeesResult {
                account_id: AccountId::new(account.account_id.clone())?,
                source: "direct_provider".into(),
                mode: "standalone".into(),
                kind: "fees".into(),
                product,
                symbol: Some(symbol.into()),
                completeness: classify_query_failure(&message),
                maker: None,
                taker: None,
                buyer: None,
                seller: None,
                standard: None,
                special: None,
                tax: None,
                discount: None,
                rpi: None,
                vip_tier: None,
                vip_tier_status: "unavailable".into(),
                observed_at_unix_nanos: Some(now_unix_nanos()),
                issues: vec![message],
            }),
        }
    }

    pub fn switch_account_model(
        &mut self,
        account_id: &str,
        target: &str,
        reason: &str,
    ) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
        let target_model = AccountModel::parse(target)
            .ok_or_else(|| format!("unsupported account model: {target}"))?;
        let mut record = self
            .registry
            .accounts
            .iter()
            .find(|record| record.account_id == account_id)
            .cloned()
            .ok_or_else(|| format!("account not found: {account_id}"))?;
        let previous = record.account_model.clone();
        if previous
            .as_deref()
            .is_some_and(|value| AccountModel::parse(value) == Some(target_model))
        {
            return Err(format!("account already uses target model: {target}").into());
        }
        record.account_model = Some(target.to_owned());
        record.status = "reconciling".into();
        self.registry.upsert_account(record.clone());
        self.registry.save(&self.registry_path)?;
        Ok(serde_json::json!({
            "account_id": account_id,
            "from_model": previous,
            "to_model": target,
            "status": "requested",
            "reason": reason,
            "account": record,
        }))
    }

    pub fn register_account(
        &mut self,
        request: RegisterAccountRequest,
    ) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
        let values = parse_field_values(&request.fields)?;
        self.registry.upsert_account(AccountBindingRecord {
            account_id: request.account_id.clone(),
            alias: request.account_id.clone(),
            broker: request.broker,
            integration_provider: request.integration_provider,
            exchange: request.exchange,
            environment: request.environment,
            remote_identity: None,
            permissions: BTreeMap::new(),
            segments: vec![request.segment.clone()],
            segment_products: BTreeMap::from([(request.segment.clone(), request.product)]),
            segment_trading_modes: request
                .trading_mode
                .map(|value| BTreeMap::from([(request.segment.clone(), value)]))
                .unwrap_or_default(),
            account_model: request.account_model,
            credential_id: None,
            credentials: Vec::new(),
            credential_role: None,
            status: "configured".into(),
            initial_balances: Vec::new(),
            fee_rate: None,
            values,
        });
        self.registry.save(&self.registry_path)?;
        Ok(serde_json::json!({
            "account_id": request.account_id,
            "status": "registered",
        }))
    }

    pub fn modify_account(
        &mut self,
        request: ModifyAccountRequest,
    ) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
        let fee_rate_requested = request.fee_rate.is_some();
        let mut record = self
            .registry
            .accounts
            .iter()
            .find(|value| value.account_id == request.account_id)
            .cloned()
            .ok_or_else(|| format!("account not found: {}", request.account_id))?;
        if let Some(value) = request.broker {
            record.broker = value;
        }
        if let Some(value) = request.integration_provider {
            record.integration_provider = value;
        }
        if let Some(value) = request.exchange {
            record.exchange = Some(value);
        }
        if let Some(value) = request.alias {
            record.alias = value;
        }
        if let Some(value) = request.environment {
            record.environment = value;
        }
        if let Some(value) = request.segment {
            record.segments = vec![value.clone()];
            let provider_product = request
                .product
                .clone()
                .ok_or("--product is required when changing --segment")?;
            record.segment_products = BTreeMap::from([(value, provider_product)]);
            record.segment_trading_modes.clear();
        } else if let Some(value) = request.product {
            let segment_key = record
                .segments
                .first()
                .cloned()
                .ok_or("account has no segment to assign the product")?;
            record.segment_products.insert(segment_key, value);
        }
        if let Some(value) = request.trading_mode {
            let segment_key = record
                .segments
                .first()
                .cloned()
                .ok_or("account has no segment to assign the trading mode")?;
            record.segment_trading_modes.insert(segment_key, value);
        }
        if request.account_model.is_some() {
            record.account_model = request.account_model;
        }
        if request.credential_id.is_some() {
            record.credential_id = request.credential_id;
        }
        if request.credential_role.is_some() {
            record.credential_role = request.credential_role;
        }
        if request.status.is_some() {
            record.status = request.status.unwrap_or_default();
        }
        if request.fee_rate.is_some() {
            record.fee_rate = request.fee_rate;
        }
        if !request.initial_balances.is_empty() {
            record.initial_balances = request.initial_balances;
        }
        record.values.extend(parse_field_values(&request.fields)?);
        if request.clear_credential {
            record.credential_id = None;
            record.credential_role = None;
            record.credentials.clear();
        }
        self.registry.upsert_account(record.clone());
        self.registry.save(&self.registry_path)?;
        let mut value = serde_json::to_value(&record)?;
        if fee_rate_requested && record.environment.eq_ignore_ascii_case("live") {
            value.as_object_mut().expect("record serializes as object").insert(
                "warnings".into(),
                serde_json::json!([
                    "fee_rate is deprecated for live accounts; use the product/symbol fees query for observed rates"
                ]),
            );
        }
        Ok(value)
    }

    pub fn simulate_account(
        &mut self,
        request: SimulateAccountRequest,
    ) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
        let record = AccountBindingRecord {
            account_id: request.account_id.clone(),
            alias: request.account_id.clone(),
            broker: "paper".into(),
            integration_provider: "paper".into(),
            exchange: Some("paper".into()),
            environment: "paper".into(),
            remote_identity: None,
            permissions: BTreeMap::new(),
            segments: vec![request.segment.clone()],
            segment_products: BTreeMap::from([(request.segment, "paper".into())]),
            segment_trading_modes: BTreeMap::new(),
            account_model: request.account_model,
            credential_id: None,
            credentials: Vec::new(),
            credential_role: None,
            status: "simulated".into(),
            initial_balances: request.initial_balances,
            fee_rate: request.fee_rate,
            values: BTreeMap::new(),
        };
        self.registry.upsert_account(record.clone());
        self.registry.save(&self.registry_path)?;
        Ok(serde_json::json!({
            "account": record,
            "mode": "paper",
            "status": "simulated",
        }))
    }

    pub fn remove_account(
        &mut self,
        account_id: &str,
    ) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
        let removed = self.registry.remove_account(account_id);
        self.registry.save(&self.registry_path)?;
        Ok(serde_json::json!({
            "account_id": account_id,
            "removed": removed,
        }))
    }

    pub fn list_credentials(&self) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
        let values: Vec<_> = self
            .credential_store
            .credentials
            .iter()
            .map(|record| {
                serde_json::json!({
                    "credential_id": record.credential_id,
                    "provider": record.provider,
                    "role": record.role,
                    "api_key": redact(&record.api_key),
                })
            })
            .collect();
        Ok(serde_json::to_value(values)?)
    }

    pub fn bind_credential(
        &mut self,
        request: BindCredentialRequest,
        credential_profile: Option<&ExternalAccountCredentialProfile>,
    ) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
        let mut record = self
            .registry
            .accounts
            .iter()
            .find(|value| value.account_id == request.account_id)
            .cloned()
            .ok_or_else(|| format!("account not found: {}", request.account_id))?;
        self.credential_store
            .credentials
            .iter()
            .find(|value| value.credential_id == request.credential_id)
            .ok_or_else(|| format!("credential not found: {}", request.credential_id))?;
        if let Some(profile) = credential_profile {
            let permissions: std::collections::BTreeSet<_> = profile
                .permissions
                .iter()
                .map(|value| value.trim().to_ascii_lowercase())
                .collect();
            if !permissions.contains("read") {
                return Err(format!(
                    "credential {} does not provide read permission",
                    request.credential_id
                )
                .into());
            }
            if request.role.trim().eq_ignore_ascii_case("trade") && !permissions.contains("trade") {
                return Err(format!(
                    "credential {} does not provide trade permission",
                    request.credential_id
                )
                .into());
            }
            if let (Some(expected), Some(actual)) = (
                record.remote_identity.as_deref(),
                profile.remote_identity.as_deref(),
            ) {
                if expected != actual {
                    return Err(format!(
                        "credential remote identity mismatch: expected {expected}, got {actual}"
                    )
                    .into());
                }
            }
            record.remote_identity = profile.remote_identity.clone();
            record.permissions = profile
                .permissions
                .iter()
                .map(|permission| (permission.clone(), "granted".into()))
                .collect();
            if !profile.segments.is_empty() {
                record.segments = profile.segments.clone();
            }
        }
        if !request.force
            && record
                .credentials
                .iter()
                .any(|value| value.name == request.name)
        {
            return Err(format!("account credential name already exists: {}", request.name).into());
        }
        record
            .credentials
            .retain(|value| value.name != request.name);
        record.credentials.push(AccountCredentialBinding {
            name: request.name.clone(),
            credential_id: request.credential_id.clone(),
            role: request.role.clone(),
        });
        if record.credential_id.is_none() {
            record.credential_id = Some(request.credential_id.clone());
            record.credential_role = Some(request.role.clone());
        }
        self.registry.upsert_account(record.clone());
        self.registry.save(&self.registry_path)?;
        Ok(serde_json::json!({
            "account_id": request.account_id,
            "name": request.name,
            "credential_id": request.credential_id,
            "role": request.role,
            "checked": credential_profile.is_some(),
            "status": "bound",
            "account": record,
        }))
    }

    pub async fn bind_credential_with_probe(
        &mut self,
        request: BindCredentialRequest,
        probe: AccountCredentialProbeRequest,
    ) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
        let credential_profile = if probe.check {
            let options = self.credential_probe_options(
                &request.account_id,
                &request.credential_id,
                &probe.connection,
            )?;
            Some(
                self.inspect_credential(&options, &probe.egress_scope_id)
                    .await
                    .map_err(|error| format!("credential check failed: {error}"))?,
            )
        } else {
            None
        };
        self.bind_credential(request, credential_profile.as_ref())
    }

    pub fn connect_account(
        &mut self,
        request: ConnectAccountRequest,
    ) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
        self.registry.upsert_account(AccountBindingRecord {
            account_id: request.account_id.clone(),
            alias: request.alias.unwrap_or_else(|| request.account_id.clone()),
            broker: request.broker,
            integration_provider: request.provider.clone(),
            exchange: Some(request.provider.clone()),
            environment: request.environment,
            remote_identity: request.remote_identity,
            permissions: request
                .permissions
                .iter()
                .map(|permission| (permission.clone(), "granted".into()))
                .collect(),
            segments: request.discovered_segments.clone(),
            segment_products: request
                .discovered_segments
                .iter()
                .map(|value| (value.clone(), value.clone()))
                .collect(),
            segment_trading_modes: request
                .trading_mode
                .map(|mode| {
                    request
                        .discovered_segments
                        .iter()
                        .map(|segment| (segment.clone(), mode.clone()))
                        .collect()
                })
                .unwrap_or_default(),
            account_model: None,
            credential_id: request.credential_id.clone(),
            credentials: request
                .credential_id
                .as_ref()
                .map(|credential_id| {
                    vec![AccountCredentialBinding {
                        name: "default".into(),
                        credential_id: credential_id.clone(),
                        role: request.credential_role.clone(),
                    }]
                })
                .unwrap_or_default(),
            credential_role: Some(request.credential_role),
            status: "connected".into(),
            initial_balances: Vec::new(),
            fee_rate: None,
            values: BTreeMap::new(),
        });
        self.registry.save(&self.registry_path)?;
        Ok(serde_json::json!({
            "account_id": request.account_id,
            "provider": request.provider,
            "segment": request.segment,
            "discovered_segments": request.discovered_segments,
            "status": "connected",
            "source": "direct_provider",
            "credential_profile": request.credential_profile,
        }))
    }

    pub async fn connect_account_from_provider(
        &mut self,
        request: ConnectAccountProviderRequest,
    ) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
        let selected_segment = request.connection.segment.clone();
        let account_id = request
            .connection
            .account_id
            .clone()
            .or_else(|| request.connection.alias.clone())
            .or_else(|| request.connection.credential_id.clone())
            .unwrap_or_else(|| format!("{}-{selected_segment}", request.connection.provider));
        let account_id = self.resolve_account_id(&account_id)?;
        let account_record = self
            .registry
            .accounts
            .iter()
            .find(|record| record.account_id == account_id)
            .cloned();
        let provider = account_record
            .as_ref()
            .map(|record| record.integration_provider.clone())
            .unwrap_or_else(|| request.connection.provider.clone());
        let product = account_record
            .as_ref()
            .and_then(|record| {
                record
                    .segments
                    .first()
                    .and_then(|segment| record.product_for_segment(segment))
                    .map(str::to_owned)
            })
            .unwrap_or_else(|| request.connection.product.clone());
        let environment = account_record
            .as_ref()
            .map(|record| record.environment.clone())
            .unwrap_or_else(|| request.connection.environment.clone());
        let credential_id = request.connection.credential_id.clone().or_else(|| {
            account_record
                .as_ref()
                .and_then(|record| record.credential_id.clone())
        });
        let credential = credential_id.as_deref().and_then(|id| {
            self.credential_store
                .credentials
                .iter()
                .find(|record| record.credential_id == id)
        });
        let paper = is_paper_or_simulated(&provider);
        let api_key = if paper {
            String::new()
        } else {
            request
                .connection
                .api_key
                .clone()
                .or_else(|| credential.and_then(|record| record.api_key_value()))
                .ok_or("an API key is required; provide --api-key or an Integration credential")?
        };
        let secret = if paper {
            String::new()
        } else {
            request
                .connection
                .secret
                .clone()
                .or_else(|| credential.and_then(|record| record.secret_value()))
                .ok_or("an API secret is required; provide --secret or an Integration credential")?
        };
        let passphrase = if request.connection.passphrase.is_empty() {
            credential
                .and_then(|record| record.passphrase_value())
                .unwrap_or_default()
        } else {
            request.connection.passphrase.clone()
        };
        let options = self.account_options(AccountOptionInput {
            provider,
            product,
            api_key,
            secret,
            passphrase,
            base_url: request.connection.base_url.clone(),
            account_id: account_id.clone(),
            segment: selected_segment,
            environment,
            account_model: account_record
                .as_ref()
                .and_then(|record| record.account_model.clone()),
            initial_balances: account_record
                .as_ref()
                .map(|record| record.initial_balances.clone())
                .unwrap_or_default(),
            host: request.connection.host.clone(),
            port: request.connection.port,
            client_id: request.connection.client_id,
            isolated_margin_symbol: account_record
                .as_ref()
                .and_then(|value| value.values.get("isolated_margin_symbol").cloned()),
            reference_database: Some(self.reference_database.clone()),
        })?;
        let broker = request
            .connection
            .broker
            .clone()
            .ok_or("--broker is required when connect creates an Account binding")?;
        let credential_profile = self
            .inspect_credential(&options, &request.egress_scope_id)
            .await
            .ok();
        let connected_role = credential
            .map(|value| value.role.clone())
            .unwrap_or_else(|| "readonly".into());
        let discovered_segments = credential_profile
            .as_ref()
            .map(|value| value.segments.clone())
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| vec![options.segment.clone()]);
        self.connect_account(ConnectAccountRequest {
            account_id,
            alias: request.connection.alias,
            broker,
            provider: options.provider,
            segment: options.segment,
            environment: options.environment,
            credential_id: request.connection.credential_id,
            credential_role: connected_role,
            trading_mode: request.connection.trading_mode,
            discovered_segments,
            remote_identity: credential_profile
                .as_ref()
                .and_then(|value| value.remote_identity.clone()),
            permissions: credential_profile
                .as_ref()
                .map(|value| value.permissions.clone())
                .unwrap_or_default(),
            credential_profile,
        })
    }

    fn credential_probe_options(
        &self,
        account_id: &str,
        credential_id: &str,
        connection: &AccountProviderConnectionArgs,
    ) -> Result<AccountOptions, Box<dyn std::error::Error>> {
        let account = self
            .registry
            .accounts
            .iter()
            .find(|value| value.account_id == account_id)
            .ok_or_else(|| format!("account not found: {account_id}"))?;
        let credential = self
            .credential_store
            .credentials
            .iter()
            .find(|value| value.credential_id == credential_id)
            .ok_or_else(|| format!("credential not found: {credential_id}"))?;
        let paper = is_paper_or_simulated(&account.integration_provider);
        let api_key = if paper {
            String::new()
        } else {
            credential
                .api_key_value()
                .ok_or("credential has no API key")?
        };
        let secret = if paper {
            String::new()
        } else {
            credential
                .secret_value()
                .ok_or("credential has no API secret")?
        };
        let passphrase = credential.passphrase_value().unwrap_or_default();
        let product = account
            .segments
            .first()
            .and_then(|segment| account.product_for_segment(segment))
            .map(str::to_owned)
            .unwrap_or_else(|| connection.product.clone());
        self.account_options(AccountOptionInput {
            provider: account.integration_provider.clone(),
            product,
            api_key,
            secret,
            passphrase,
            base_url: connection.base_url.clone(),
            account_id: account.account_id.clone(),
            segment: account
                .segments
                .first()
                .cloned()
                .unwrap_or_else(|| connection.segment.clone()),
            environment: account.environment.clone(),
            account_model: account.account_model.clone(),
            initial_balances: account.initial_balances.clone(),
            host: connection.host.clone(),
            port: connection.port,
            client_id: connection.client_id,
            isolated_margin_symbol: account.values.get("isolated_margin_symbol").cloned(),
            reference_database: None,
        })
    }

    fn account_options(
        &self,
        input: AccountOptionInput,
    ) -> Result<AccountOptions, Box<dyn std::error::Error>> {
        let base_url = if input.base_url.trim().is_empty() {
            default_rest_endpoint(&input.provider, &input.product)?.to_owned()
        } else {
            input.base_url
        };
        Ok(AccountOptions {
            provider: input.provider,
            product: input.product,
            api_key: input.api_key.into(),
            secret: input.secret.into(),
            passphrase: input.passphrase.into(),
            base_url,
            account_id: input.account_id,
            segment: input.segment,
            environment: input.environment,
            account_model: input.account_model,
            initial_balances: input.initial_balances,
            host: input.host,
            port: input.port,
            client_id: input.client_id,
            isolated_margin_symbol: input.isolated_margin_symbol,
            reference_database: input.reference_database,
        })
    }

    async fn inspect_credential(
        &self,
        options: &AccountOptions,
        egress_scope_id: &str,
    ) -> Result<ExternalAccountCredentialProfile, String> {
        inspect_account_credential(
            options,
            Some(self.provider_quota_ledger_path.clone()),
            egress_scope_id,
        )
        .await
    }

    pub fn create_credential(
        &mut self,
        request: CreateCredentialRequest,
    ) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
        self.credential_store.upsert(CredentialRecord {
            credential_id: request.credential_id.clone(),
            provider: request.provider,
            role: request.role,
            api_key: request.api_key.unwrap_or_default(),
            secret: request.secret.unwrap_or_default(),
            passphrase: request.passphrase,
        });
        self.credential_store.save(&self.credentials_path)?;
        Ok(serde_json::json!({
            "credential_id": request.credential_id,
            "status": "created",
        }))
    }

    pub fn delete_credential(
        &mut self,
        credential_id: &str,
        force: bool,
    ) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
        if !force
            && self
                .registry
                .accounts
                .iter()
                .any(|account| account.credential_id.as_deref() == Some(credential_id))
        {
            return Err(format!(
                "credential is bound to an account: {credential_id}; use --force to delete"
            )
            .into());
        }
        let removed = self.credential_store.remove(credential_id);
        self.credential_store.save(&self.credentials_path)?;
        Ok(serde_json::json!({
            "credential_id": credential_id,
            "removed": removed,
        }))
    }

    pub fn show_credential(
        &self,
        credential_id: &str,
        reveal_secrets: bool,
    ) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
        let credential = self
            .credential_store
            .credentials
            .iter()
            .find(|record| record.credential_id == credential_id)
            .ok_or_else(|| format!("credential not found: {credential_id}"))?;
        Ok(serde_json::json!({
            "credential_id": credential.credential_id,
            "provider": credential.provider,
            "role": credential.role,
            "api_key": if reveal_secrets { credential.api_key_value().unwrap_or_default() } else { redact(&credential.api_key) },
            "secret": if reveal_secrets { credential.secret_value().unwrap_or_default() } else { "***".to_string() },
            "passphrase": if reveal_secrets { credential.passphrase_value().unwrap_or_default() } else { "***".to_string() },
        }))
    }

    pub fn schemas(&self) -> serde_json::Value {
        serde_json::json!({
            "binance": {"credential_fields": ["api_key", "api_secret"], "segments": ["spot", "cross_margin", "isolated_margin", "usd_m_futures", "coin_m_futures", "funding", "options"]},
            "okx": {"credential_fields": ["api_key", "api_secret", "passphrase"], "segments": ["spot", "cross_margin", "isolated_margin", "swap", "futures", "options"]},
            "ibkr": {"credential_fields": [], "connection_fields": ["host", "port", "client_id"], "segments": ["equity"]},
            "paper": {"credential_fields": [], "segments": ["spot", "margin", "futures"]},
        })
    }

    pub fn schema(&self, provider: &str) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
        let provider = provider.to_ascii_lowercase();
        let value = match provider.as_str() {
            "binance" => {
                serde_json::json!({"provider":"binance","credential_fields":["api_key","api_secret"],"segments":["spot","cross_margin","isolated_margin","usd_m_futures","coin_m_futures","funding","options"]})
            },
            "okx" | "okex" => {
                serde_json::json!({"provider":"okx","credential_fields":["api_key","api_secret","passphrase"],"segments":["spot","cross_margin","isolated_margin","swap","futures","options"]})
            },
            "ibkr" => {
                serde_json::json!({"provider":"ibkr","credential_fields":[],"connection_fields":["host","port","client_id"],"segments":["equity"]})
            },
            "paper" => {
                serde_json::json!({"provider":"paper","credential_fields":[],"segments":["spot","margin","futures"]})
            },
            _ => return Err(format!("unsupported provider: {provider}").into()),
        };
        Ok(value)
    }

    pub fn doctor(
        &self,
        account_id: Option<&str>,
    ) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
        let selected_account_id = account_id
            .map(|value| self.resolve_account_id(value))
            .transpose()?;
        let selected_accounts = self
            .registry
            .accounts
            .iter()
            .filter(|account| {
                selected_account_id
                    .as_deref()
                    .is_none_or(|value| value == account.account_id)
            })
            .collect::<Vec<_>>();
        let mut issues: Vec<_> = self
            .registry
            .accounts
            .iter()
            .filter(|account| {
                selected_account_id
                    .as_deref()
                    .is_none_or(|value| value == account.account_id)
            })
            .filter(|account| {
                account.environment == "live"
                    && !self.credential_store.credentials.iter().any(|credential| {
                        credential.provider == account.integration_provider
                            || account
                                .credential_id
                                .as_deref()
                                .is_some_and(|id| credential.credential_id == id)
                    })
            })
            .map(|account| {
                format!(
                    "{}: live account has no matching credential",
                    account.account_id
                )
            })
            .collect();
        for account in &selected_accounts {
            issues.extend(account_configuration_issues(account));
        }
        Ok(serde_json::json!({
            "accounts": selected_accounts,
            "issues": issues,
            "runtime": serde_json::Map::new(),
        }))
    }

    pub fn resolve_account_id(&self, value: &str) -> Result<String, Box<dyn std::error::Error>> {
        resolve_account_id(&self.registry, value)
    }

    fn account(
        &self,
        account_id: &str,
    ) -> Result<&AccountBindingRecord, Box<dyn std::error::Error>> {
        self.registry
            .accounts
            .iter()
            .find(|record| record.account_id == account_id)
            .ok_or_else(|| format!("account not found: {account_id}").into())
    }

    fn direct_query_options(
        &self,
        account: &AccountBindingRecord,
    ) -> Result<AccountOptions, Box<dyn std::error::Error>> {
        let provider = account.integration_provider.trim().to_ascii_lowercase();
        let credential_id = account
            .credentials
            .iter()
            .find(|binding| binding.role.eq_ignore_ascii_case("readonly"))
            .map(|binding| binding.credential_id.as_str())
            .or(account.credential_id.as_deref());
        let credential = credential_id.and_then(|credential_id| {
            self.credential_store
                .credentials
                .iter()
                .find(|credential| credential.credential_id == credential_id)
        });
        if provider != "ibkr" && credential.is_none() {
            return Err(
                format!("account {} has no readable credential", account.account_id).into(),
            );
        }
        if let Some(credential) = credential {
            if !credential.provider.eq_ignore_ascii_case(&provider) {
                return Err(format!(
                    "credential {} belongs to provider {}, not {}",
                    credential.credential_id, credential.provider, provider
                )
                .into());
            }
        }
        let product = account
            .segments
            .first()
            .and_then(|segment| account.product_for_segment(segment))
            .unwrap_or("spot")
            .to_owned();
        let base_url = account
            .values
            .get("base_url")
            .cloned()
            .unwrap_or_else(String::new);
        self.account_options(AccountOptionInput {
            provider,
            product,
            api_key: credential
                .and_then(|value| value.api_key_value())
                .unwrap_or_default(),
            secret: credential
                .and_then(|value| value.secret_value())
                .unwrap_or_default(),
            passphrase: credential
                .and_then(|value| value.passphrase_value())
                .unwrap_or_default(),
            base_url,
            account_id: account
                .remote_identity
                .clone()
                .unwrap_or_else(|| account.account_id.clone()),
            segment: account
                .segments
                .first()
                .cloned()
                .unwrap_or_else(|| "spot".into()),
            environment: account.environment.clone(),
            account_model: account.account_model.clone(),
            initial_balances: Vec::new(),
            host: account
                .values
                .get("host")
                .cloned()
                .unwrap_or_else(|| "127.0.0.1".into()),
            port: account
                .values
                .get("port")
                .map(|value| value.parse())
                .transpose()?
                .unwrap_or(4002),
            client_id: account
                .values
                .get("client_id")
                .map(|value| value.parse())
                .transpose()?
                .unwrap_or_default(),
            isolated_margin_symbol: account.values.get("isolated_margin_symbol").cloned(),
            reference_database: None,
        })
    }

    fn local_query_account(
        &self,
        account_id: &str,
    ) -> Result<&AccountBindingRecord, Box<dyn std::error::Error>> {
        let account = self
            .registry
            .accounts
            .iter()
            .find(|record| record.account_id == account_id)
            .ok_or_else(|| format!("account not found: {account_id}"))?;
        if !is_paper_or_simulated(&account.broker)
            && !is_paper_or_simulated(&account.integration_provider)
            && !is_paper_or_simulated(&account.environment)
        {
            return Err(format!(
                "`kairos account` standalone queries only support local paper/simulated accounts for now; account {account_id} requires a direct provider query service or a connected component"
            )
            .into());
        }
        Ok(account)
    }

    fn local_segments(
        &self,
        account: &AccountBindingRecord,
    ) -> Result<Vec<serde_json::Value>, Box<dyn std::error::Error>> {
        let mut segments = Vec::new();
        for segment in &account.segments {
            let mut balances = Vec::new();
            for value in &account.initial_balances {
                if let Some(balance) = local_balance(segment, value)? {
                    balances.push(serde_json::to_value(balance)?);
                }
            }
            segments.push(serde_json::json!({
                "segment": segment,
                "balances": balances,
                "positions": [],
            }));
        }
        Ok(segments)
    }

    fn local_balances_for(
        &self,
        account: &AccountBindingRecord,
    ) -> Result<Vec<AccountBalanceItem>, Box<dyn std::error::Error>> {
        let mut balances = Vec::new();
        for segment in &account.segments {
            for value in &account.initial_balances {
                if let Some(balance) = local_balance(segment, value)? {
                    balances.push(balance);
                }
            }
        }
        Ok(balances)
    }
}

fn account_configuration_issues(account: &AccountBindingRecord) -> Vec<String> {
    let mut issues = Vec::new();
    let normalized_model = account
        .account_model
        .as_deref()
        .and_then(AccountModel::parse);
    if account.account_model.is_some() && normalized_model.is_none() {
        issues.push(format!(
            "{}: configured account_model is not a supported canonical model",
            account.account_id
        ));
    }
    let has_derivatives = account.segments.iter().any(|segment| {
        account.product_for_segment(segment).is_some_and(|product| {
            matches!(
                product
                    .trim()
                    .to_ascii_lowercase()
                    .replace('_', "-")
                    .as_str(),
                "usd-m-futures" | "coin-m-futures" | "options"
            )
        })
    });
    if account.integration_provider.eq_ignore_ascii_case("binance")
        && has_derivatives
        && normalized_model.is_none()
    {
        issues.push(format!(
            "{}: derivative segments have no explicit account_model; run overview for profile discovery and configure the observed canonical model",
            account.account_id
        ));
    }
    if normalized_model == Some(AccountModel::PortfolioMargin) && !has_derivatives {
        issues.push(format!(
            "{}: portfolio_margin is configured without a derivatives/options segment",
            account.account_id
        ));
    }
    if account.environment.eq_ignore_ascii_case("live") && account.fee_rate.is_some() {
        issues.push(format!(
            "{}: fee_rate is deprecated for live accounts and is not treated as observed fee truth",
            account.account_id
        ));
    }
    issues
}

fn local_balance(
    segment: &str,
    value: &str,
) -> Result<Option<AccountBalanceItem>, Box<dyn std::error::Error>> {
    let Some((asset, total)) = value.split_once('=') else {
        return Err(format!("initial balance must be ASSET=AMOUNT: {value}").into());
    };
    let asset = asset.trim();
    let total = total.trim();
    if asset.is_empty() || total.is_empty() {
        return Err(format!("initial balance must be ASSET=AMOUNT: {value}").into());
    }
    let total = total.parse::<DecimalParts>()?;
    Ok(Some(AccountBalanceItem {
        segment: SegmentKey::new(segment.to_owned())?,
        role: "wallet".into(),
        asset: Currency::new(asset.to_ascii_uppercase())?,
        total,
        available: Some(total),
        locked: Some(DecimalParts::default()),
        borrowed: None,
        interest: None,
    }))
}

fn append_external_balances(
    output: &mut Vec<AccountBalanceItem>,
    segment: SegmentKey,
    role: &str,
    balances: Vec<kairos_conflux::ExternalBalance>,
    include_zero: bool,
) -> Result<(), kairos_primitives::DomainTypeError> {
    for balance in balances {
        let total = decimal_parts(balance.total)?;
        if !include_zero && total.mantissa() == 0 {
            continue;
        }
        output.push(AccountBalanceItem {
            segment: segment.clone(),
            role: role.into(),
            asset: balance.asset_code,
            total,
            available: balance.available.map(decimal_parts).transpose()?,
            locked: balance.locked.map(decimal_parts).transpose()?,
            borrowed: balance.borrowed.map(decimal_parts).transpose()?,
            interest: balance.interest.map(decimal_parts).transpose()?,
        });
    }
    Ok(())
}

fn map_earn_holding(
    segment: SegmentKey,
    position: kairos_conflux::EarnPosition,
) -> Result<AccountEarnHoldingItem, kairos_primitives::DomainTypeError> {
    let family = match &position.family {
        kairos_conflux::EarnProductFamily::Flexible => "flexible".into(),
        kairos_conflux::EarnProductFamily::Locked => "locked".into(),
        kairos_conflux::EarnProductFamily::Staking => "staking".into(),
        kairos_conflux::EarnProductFamily::YieldBearingAsset => "yield_bearing_asset".into(),
        kairos_conflux::EarnProductFamily::Other(value) => value.clone(),
    };
    let liquidity = match position.family {
        kairos_conflux::EarnProductFamily::Flexible => "immediate".into(),
        kairos_conflux::EarnProductFamily::Locked => position
            .matures_at_unix_nanos
            .map(|value| format!("fixed_term:{}", value.get()))
            .unwrap_or_else(|| "fixed_term".into()),
        _ => "unknown".into(),
    };
    let state = match position.state {
        kairos_conflux::EarnPositionState::Active => "active".into(),
        kairos_conflux::EarnPositionState::Redeeming => "redeeming".into(),
        kairos_conflux::EarnPositionState::Redeemed => "redeemed".into(),
        kairos_conflux::EarnPositionState::Unknown(value) => format!("unknown:{value}"),
    };
    let accrued_rewards = position
        .accrued_rewards
        .into_iter()
        .map(|reward| {
            Ok(AccountEarnRewardItem {
                asset: reward.asset,
                amount: DecimalParts::new(reward.amount.mantissa(), reward.amount.scale())?,
                component: reward
                    .component
                    .map(|component| format!("{component:?}").to_ascii_lowercase()),
            })
        })
        .collect::<Result<Vec<_>, kairos_primitives::DomainTypeError>>()?;
    Ok(AccountEarnHoldingItem {
        segment,
        participant_position_id: position.participant_position_id,
        product_id: position.product_id,
        asset: position.asset,
        family,
        principal: DecimalParts::new(position.principal.mantissa(), position.principal.scale())?,
        accrued_rewards,
        redeemable_amount: position
            .redeemable_amount
            .map(|value| DecimalParts::new(value.mantissa(), value.scale()))
            .transpose()?,
        liquidity,
        subscribed_at_unix_nanos: position.subscribed_at_unix_nanos.map(|value| value.get()),
        matures_at_unix_nanos: position.matures_at_unix_nanos.map(|value| value.get()),
        state,
        observed_at_unix_nanos: position.observed_at_unix_nanos.map(|value| value.get()),
    })
}

fn decimal_parts(
    value: kairos_conflux::ExternalDecimal,
) -> Result<DecimalParts, kairos_primitives::DomainTypeError> {
    DecimalParts::new(value.mantissa, value.scale)
}

fn map_open_order(
    segment: SegmentKey,
    order: ExternalOrder,
) -> Result<AccountOpenOrderItem, kairos_primitives::DomainTypeError> {
    Ok(AccountOpenOrderItem {
        segment,
        order_id: order.order_id.to_string(),
        client_order_id: order.client_order_id.map(|value| value.to_string()),
        symbol: order.symbol.to_string(),
        side: format!("{:?}", order.side).to_ascii_lowercase(),
        order_type: format!("{:?}", order.order_type).to_ascii_lowercase(),
        status: format!("{:?}", order.status).to_ascii_lowercase(),
        quantity: decimal_parts(order.quantity)?,
        filled_quantity: decimal_parts(order.filled_quantity)?,
        average_fill_price: order.average_fill_price.map(decimal_parts).transpose()?,
        occurred_at_unix_nanos: order.occurred_at_unix_nanos.map(|value| value.get()),
    })
}

fn resolve_fee_product(
    account: &AccountBindingRecord,
    requested: &str,
) -> Result<String, Box<dyn std::error::Error>> {
    let normalized = requested.trim().to_ascii_lowercase().replace('_', "-");
    account
        .segments
        .iter()
        .find_map(|segment| {
            let product = account.product_for_segment(segment).unwrap_or(segment);
            ((segment.trim().to_ascii_lowercase().replace('_', "-") == normalized)
                || (product.trim().to_ascii_lowercase().replace('_', "-") == normalized))
                .then(|| product.to_owned())
        })
        .ok_or_else(|| {
            format!(
                "account {} does not configure fee product {requested}",
                account.account_id
            )
            .into()
        })
}

fn map_fee_schedule(
    account: &AccountBindingRecord,
    product: String,
    schedule: ExternalFeeSchedule,
    vip_level: Option<u32>,
) -> Result<AccountFeesResult, Box<dyn std::error::Error>> {
    Ok(AccountFeesResult {
        account_id: AccountId::new(account.account_id.clone())?,
        source: "direct_provider".into(),
        mode: "standalone".into(),
        kind: "fees".into(),
        product,
        symbol: Some(schedule.symbol.to_string()),
        completeness: AccountQueryCompleteness::Complete,
        maker: Some(decimal_parts(schedule.maker)?),
        taker: Some(decimal_parts(schedule.taker)?),
        buyer: schedule.buyer.map(decimal_parts).transpose()?,
        seller: schedule.seller.map(decimal_parts).transpose()?,
        standard: schedule.standard.map(map_fee_component).transpose()?,
        special: schedule.special.map(map_fee_component).transpose()?,
        tax: schedule.tax.map(map_fee_component).transpose()?,
        discount: schedule
            .discount
            .map(
                |discount| -> Result<AccountFeeDiscount, kairos_primitives::DomainTypeError> {
                    Ok(AccountFeeDiscount {
                        enabled_for_account: discount.enabled_for_account,
                        enabled_for_symbol: discount.enabled_for_symbol,
                        asset: discount.asset.map(|asset| asset.to_string()),
                        rate: discount.rate.map(decimal_parts).transpose()?,
                    })
                },
            )
            .transpose()?,
        rpi: schedule.rpi.map(decimal_parts).transpose()?,
        vip_tier: vip_level.map(|level| format!("VIP {level}")),
        vip_tier_status: if vip_level.is_some() {
            "observed"
        } else {
            "included_in_observed_rate"
        }
        .into(),
        observed_at_unix_nanos: Some(now_unix_nanos()),
        issues: Vec::new(),
    })
}

fn map_fee_component(
    component: ExternalFeeComponent,
) -> Result<AccountFeeComponent, kairos_primitives::DomainTypeError> {
    Ok(AccountFeeComponent {
        maker: component.maker.map(decimal_parts).transpose()?,
        taker: component.taker.map(decimal_parts).transpose()?,
        buyer: component.buyer.map(decimal_parts).transpose()?,
        seller: component.seller.map(decimal_parts).transpose()?,
    })
}

fn classify_query_failure(message: &str) -> AccountQueryCompleteness {
    let message = message.to_ascii_lowercase();
    if message.contains("permission")
        || message.contains("unauthorized")
        || message.contains("api-key")
        || message.contains("api key")
        || message.contains("signature")
    {
        AccountQueryCompleteness::Unauthorized
    } else if message.contains("does not expose") || message.contains("not supported") {
        AccountQueryCompleteness::Unsupported
    } else {
        AccountQueryCompleteness::Unavailable
    }
}

fn aggregate_outcomes(
    succeeded: u64,
    outcomes: &[AccountSegmentQueryOutcome],
) -> AccountQueryCompleteness {
    if outcomes
        .iter()
        .all(|outcome| outcome.outcome == AccountQueryCompleteness::Complete)
    {
        return AccountQueryCompleteness::Complete;
    }
    if succeeded > 0 {
        return AccountQueryCompleteness::Partial;
    }
    if outcomes
        .iter()
        .all(|outcome| outcome.outcome == AccountQueryCompleteness::Unsupported)
    {
        AccountQueryCompleteness::Unsupported
    } else if outcomes
        .iter()
        .all(|outcome| outcome.outcome == AccountQueryCompleteness::Unauthorized)
    {
        AccountQueryCompleteness::Unauthorized
    } else {
        AccountQueryCompleteness::Unavailable
    }
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .try_into()
        .unwrap_or(u64::MAX)
}

fn selected_segments(
    account: &AccountBindingRecord,
    requested: &[String],
) -> Result<Vec<String>, Box<dyn std::error::Error>> {
    if requested.is_empty() {
        return Ok(account.segments.clone());
    }
    let mut selected = Vec::new();
    for segment in requested {
        if !account.segments.iter().any(|value| value == segment) {
            return Err(format!(
                "account {} does not have segment {segment}",
                account.account_id
            )
            .into());
        }
        if !selected.contains(segment) {
            selected.push(segment.clone());
        }
    }
    Ok(selected)
}

pub struct RegisterAccountRequest {
    pub account_id: String,
    pub broker: String,
    pub integration_provider: String,
    pub environment: String,
    pub segment: String,
    pub product: String,
    pub trading_mode: Option<String>,
    pub account_model: Option<String>,
    pub exchange: Option<String>,
    pub fields: Vec<String>,
}

pub struct ModifyAccountRequest {
    pub account_id: String,
    pub broker: Option<String>,
    pub integration_provider: Option<String>,
    pub exchange: Option<String>,
    pub alias: Option<String>,
    pub environment: Option<String>,
    pub segment: Option<String>,
    pub product: Option<String>,
    pub trading_mode: Option<String>,
    pub account_model: Option<String>,
    pub credential_id: Option<String>,
    pub credential_role: Option<String>,
    pub status: Option<String>,
    pub fee_rate: Option<String>,
    pub initial_balances: Vec<String>,
    pub clear_credential: bool,
    pub fields: Vec<String>,
}

pub struct SimulateAccountRequest {
    pub account_id: String,
    pub segment: String,
    pub account_model: Option<String>,
    pub initial_balances: Vec<String>,
    pub fee_rate: Option<String>,
}

pub struct CreateCredentialRequest {
    pub credential_id: String,
    pub provider: String,
    pub role: String,
    pub api_key: Option<String>,
    pub secret: Option<String>,
    pub passphrase: String,
}

pub struct BindCredentialRequest {
    pub account_id: String,
    pub name: String,
    pub credential_id: String,
    pub role: String,
    pub force: bool,
}

pub struct AccountCredentialProbeRequest {
    pub check: bool,
    pub egress_scope_id: String,
    pub connection: AccountProviderConnectionArgs,
}

pub struct ConnectAccountProviderRequest {
    pub egress_scope_id: String,
    pub connection: AccountProviderConnectionArgs,
}

#[derive(Clone)]
pub struct AccountProviderConnectionArgs {
    pub provider: String,
    pub broker: Option<String>,
    pub product: String,
    pub environment: String,
    pub account_id: Option<String>,
    pub alias: Option<String>,
    pub credential_id: Option<String>,
    pub trading_mode: Option<String>,
    pub api_key: Option<String>,
    pub secret: Option<String>,
    pub passphrase: String,
    pub base_url: String,
    pub segment: String,
    pub host: String,
    pub port: u16,
    pub client_id: i32,
}

pub struct ConnectAccountRequest {
    pub account_id: String,
    pub alias: Option<String>,
    pub broker: String,
    pub provider: String,
    pub segment: String,
    pub environment: String,
    pub credential_id: Option<String>,
    pub credential_role: String,
    pub trading_mode: Option<String>,
    pub discovered_segments: Vec<String>,
    pub remote_identity: Option<String>,
    pub permissions: Vec<String>,
    pub credential_profile: Option<ExternalAccountCredentialProfile>,
}

pub fn resolve_account_id(
    registry: &AccountRegistry,
    value: &str,
) -> Result<String, Box<dyn std::error::Error>> {
    if registry
        .accounts
        .iter()
        .any(|record| record.account_id == value)
    {
        return Ok(value.to_owned());
    }
    let matches: Vec<_> = registry
        .accounts
        .iter()
        .filter(|record| record.alias == value)
        .map(|record| record.account_id.clone())
        .collect();
    match matches.as_slice() {
        [account_id] => Ok(account_id.clone()),
        [] => Ok(value.to_owned()),
        _ => Err(format!("account alias is ambiguous: {value}").into()),
    }
}

pub fn parse_field_values(
    values: &[String],
) -> Result<BTreeMap<String, String>, Box<dyn std::error::Error>> {
    let mut fields = BTreeMap::new();
    for value in values {
        let (key, field_value) = value
            .split_once('=')
            .ok_or_else(|| format!("field must be key=value: {value}"))?;
        let key = key.trim();
        if key.is_empty() {
            return Err(format!("field key is empty: {value}").into());
        }
        fields.insert(key.to_owned(), field_value.to_owned());
    }
    Ok(fields)
}

pub fn redact(value: &str) -> String {
    if value.len() <= 4 {
        return "****".into();
    }
    format!("{}****{}", &value[..2], &value[value.len() - 2..])
}

struct AccountOptionInput {
    provider: String,
    product: String,
    api_key: String,
    secret: String,
    passphrase: String,
    base_url: String,
    account_id: String,
    segment: String,
    environment: String,
    account_model: Option<String>,
    initial_balances: Vec<String>,
    host: String,
    port: u16,
    client_id: i32,
    isolated_margin_symbol: Option<String>,
    reference_database: Option<PathBuf>,
}

fn is_paper_or_simulated(value: &str) -> bool {
    matches!(
        value.trim().to_ascii_lowercase().as_str(),
        "paper" | "simulated"
    )
}

fn mask_identity(value: &str) -> String {
    let characters = value.chars().collect::<Vec<_>>();
    if characters.len() <= 4 {
        return "****".into();
    }
    format!(
        "{}****{}",
        characters[..2].iter().collect::<String>(),
        characters[characters.len() - 2..]
            .iter()
            .collect::<String>()
    )
}

fn account_model_name(value: kairos_conflux::ExternalAccountModel) -> String {
    match value {
        kairos_conflux::ExternalAccountModel::NoMargin => "no_margin",
        kairos_conflux::ExternalAccountModel::Margin => "margin",
        kairos_conflux::ExternalAccountModel::Contract => "contract",
        kairos_conflux::ExternalAccountModel::ContractUnified => "contract_unified",
        kairos_conflux::ExternalAccountModel::Unified => "unified",
        kairos_conflux::ExternalAccountModel::PortfolioMargin => "portfolio_margin",
    }
    .into()
}

fn overview_profile(
    configured_account_model: Option<String>,
    segments: Vec<AccountSegmentProfileItem>,
    explicit_profile: Option<crate::composition::account::ObservedAccountProfile>,
    provider_unified: Option<bool>,
) -> AccountOverviewProfile {
    let observed_models = segments
        .iter()
        .filter_map(|segment| segment.observed_account_model.clone())
        .collect::<std::collections::BTreeSet<_>>();
    let observed_account_model = explicit_profile
        .as_ref()
        .map(|profile| profile.account_model.clone())
        .or_else(|| {
            (observed_models.len() == 1)
                .then(|| observed_models.first().expect("one observed model").clone())
                .or_else(|| (observed_models.len() > 1).then(|| "multiple".into()))
        });
    let provider_models = segments
        .iter()
        .filter_map(|segment| segment.provider_account_model.clone())
        .collect::<std::collections::BTreeSet<_>>();
    let provider_account_model = explicit_profile
        .as_ref()
        .map(|profile| profile.provider_account_model.clone())
        .or_else(|| {
            ["portfolio_margin_pro", "portfolio_margin"]
                .into_iter()
                .find(|candidate| provider_models.contains(*candidate))
                .map(str::to_owned)
                .or_else(|| {
                    (provider_models.len() == 1)
                        .then(|| provider_models.first().expect("one provider model").clone())
                })
                .or_else(|| (provider_models.len() > 1).then(|| "multiple".into()))
        });
    let configured_model = configured_account_model
        .as_deref()
        .and_then(AccountModel::parse);
    let observed_model = observed_account_model
        .as_deref()
        .and_then(AccountModel::parse);
    let model_match = match (configured_model, observed_model) {
        (Some(configured), Some(observed)) if configured == observed => "match",
        (Some(_), Some(_)) => "mismatch",
        (None, _) => "not_configured",
        (Some(_), None) => "not_observed",
    }
    .into();
    let unified = explicit_profile
        .as_ref()
        .map(|_| true)
        .or(provider_unified)
        .or_else(|| {
            configured_account_model
                .as_deref()
                .or_else(|| {
                    (observed_models.len() == 1)
                        .then(|| observed_models.first().map(String::as_str))
                        .flatten()
                })
                .and_then(AccountModel::parse)
                .map(|model| {
                    matches!(
                        model,
                        AccountModel::ContractUnified
                            | AccountModel::Unified
                            | AccountModel::PortfolioMargin
                    )
                })
        });
    let margin_mode = segments
        .iter()
        .find_map(|segment| segment.margin_mode.clone());
    let position_mode = segments
        .iter()
        .find_map(|segment| segment.position_mode.clone());
    AccountOverviewProfile {
        configured_account_model,
        observed_account_model,
        provider_account_model,
        model_match,
        unified,
        margin_mode,
        position_mode,
        segments,
    }
}

fn query_completeness(
    segments_succeeded: u64,
    segments_requested: u64,
) -> AccountQueryCompleteness {
    if segments_succeeded == segments_requested {
        AccountQueryCompleteness::Complete
    } else if segments_succeeded == 0 {
        AccountQueryCompleteness::Unavailable
    } else {
        AccountQueryCompleteness::Partial
    }
}

#[cfg(test)]
mod tests {
    use kairos_primitives::account::{AccountId, SegmentKey};
    use kairos_primitives::decimal::DecimalParts;
    use kairos_primitives::reference::Currency;
    use serde_json::json;

    use super::{
        AccountBalanceItem, AccountBalancesResult, AccountListItem, AccountListResult,
        AccountQueryCompleteness, AccountSegmentProfileItem, AccountSegmentQueryOutcome,
        CliAccountApplication, account_capabilities, account_configuration_issues,
        classify_query_failure, overview_profile,
    };
    use crate::composition::registry::AccountBindingRecord;

    #[test]
    fn account_list_result_exposes_only_list_semantics() {
        let record: AccountBindingRecord = serde_json::from_value(json!({
            "account_id": "main",
            "alias": "primary",
            "broker": "custodian-x",
            "integration_provider": "binance",
            "environment": "live",
            "permissions": {"read": "granted", "trade": "granted"},
            "segments": ["spot"],
            "account_model": null,
            "credential_id": "binance-readonly",
            "credential_role": "readonly",
            "status": "configured",
            "values": {"provider_payload": "internal"}
        }))
        .unwrap();
        let item = AccountListItem::try_from(&record).unwrap();
        let value = serde_json::to_value(AccountListResult {
            accounts: vec![item],
            count: 1,
        })
        .unwrap();

        assert_eq!(value["accounts"][0]["account_id"], "main");
        assert_eq!(value["accounts"][0]["broker"], "custodian-x");
        assert_eq!(value["accounts"][0]["integration_provider"], "binance");
        // Retained for one compatibility cycle; business identity is broker.
        assert_eq!(value["accounts"][0]["provider"], "binance");
        assert_eq!(
            value["accounts"][0]["configured_credential_role"],
            "readonly"
        );
        assert_eq!(value["accounts"][0]["segments"], json!(["spot"]));
        assert_eq!(value["accounts"][0]["products"], json!(["spot"]));
        assert_eq!(value["count"], 1);
        assert_eq!(account_capabilities(&record), vec!["read"]);
        for internal in [
            "values",
            "permissions",
            "credentials",
            "segment_products",
            "credential_role",
        ] {
            assert!(value["accounts"][0].get(internal).is_none());
        }
    }

    #[test]
    fn trading_binding_requires_segment_and_selects_permission_appropriate_credential() {
        let directory = tempfile::tempdir().unwrap();
        let workspace =
            kairos_workspace::Workspace::init(directory.path(), "binding-test").expect("workspace");
        let account_dir = workspace.root().join("config/accounts");
        std::fs::create_dir_all(&account_dir).unwrap();
        std::fs::write(
            account_dir.join("accounts.toml"),
            r#"
[account]
id = "main"
broker = "binance"
integration_provider = "binance"
environment = "live"
model = "contract"

[segments.spot]
product_family = "spot"

[segments.usd_m]
product_family = "usd_m_futures"

[credentials.reader]
ref = "binance-read"
role = "readonly"

[credentials.trader]
ref = "binance-trade"
role = "trade"
"#,
        )
        .unwrap();
        let application = CliAccountApplication::open(&workspace).unwrap();

        let error = application
            .trading_binding("main", None, "read")
            .expect_err("multiple segments must not choose the first")
            .to_string();
        assert!(error.contains("--segment is required"), "{error}");

        let read = application
            .trading_binding("main", Some("spot"), "read")
            .unwrap();
        assert_eq!(read.segment_key.as_str(), "spot");
        assert_eq!(read.credential_id.as_deref(), Some("binance-read"));

        let trade = application
            .trading_binding("main", Some("usd_m"), "trade")
            .unwrap();
        assert_eq!(trade.provider_product, "usd_m_futures");
        assert_eq!(trade.credential_id.as_deref(), Some("binance-trade"));
        assert_eq!(trade.credential_role, "trade");
        let serialized = serde_json::to_value(trade).unwrap();
        assert!(serialized.get("api_key").is_none());
        assert!(serialized.get("secret").is_none());
    }

    #[test]
    fn overview_profile_distinguishes_match_mismatch_and_unknown() {
        let segment = |observed: Option<&str>| AccountSegmentProfileItem {
            segment: SegmentKey::new("usd_m_futures").unwrap(),
            source: "direct_provider".into(),
            freshness: "fresh".into(),
            completeness: AccountQueryCompleteness::Complete,
            observed_at_unix_nanos: Some(1),
            issue: None,
            configured_account_model: None,
            observed_account_model: observed.map(str::to_owned),
            provider_account_model: None,
            margin_mode: None,
            position_mode: None,
        };
        let matching = overview_profile(
            Some("portfolio_margin".into()),
            vec![segment(Some("portfolio_margin"))],
            None,
            None,
        );
        assert_eq!(matching.model_match, "match");
        assert_eq!(matching.unified, Some(true));

        let mismatching = overview_profile(
            Some("contract".into()),
            vec![segment(None)],
            Some(crate::composition::account::ObservedAccountProfile {
                account_model: "portfolio_margin".into(),
                provider_account_model: "portfolio_margin_pro".into(),
            }),
            None,
        );
        assert_eq!(mismatching.model_match, "mismatch");
        assert_eq!(
            mismatching.provider_account_model.as_deref(),
            Some("portfolio_margin_pro")
        );

        let not_observed = overview_profile(None, vec![segment(None)], None, None);
        assert_eq!(not_observed.model_match, "not_configured");
        assert_eq!(not_observed.unified, None);

        let multiple = overview_profile(
            None,
            vec![segment(Some("no_margin")), segment(Some("contract"))],
            None,
            None,
        );
        assert_eq!(multiple.observed_account_model.as_deref(), Some("multiple"));
        assert_eq!(multiple.unified, None);

        let provider_disabled = overview_profile(
            None,
            vec![segment(Some("no_margin")), segment(Some("contract"))],
            None,
            Some(false),
        );
        assert_eq!(provider_disabled.unified, Some(false));
    }

    #[test]
    fn doctor_flags_missing_derivative_model_and_live_registry_fee() {
        let record: AccountBindingRecord = serde_json::from_value(json!({
            "account_id": "live-main",
            "alias": "main",
            "broker": "binance",
            "integration_provider": "binance",
            "environment": "live",
            "segments": ["usd_m_futures"],
            "segment_products": {"usd_m_futures": "usd_m_futures"},
            "account_model": null,
            "fee_rate": "0.001"
        }))
        .unwrap();

        let issues = account_configuration_issues(&record).join("\n");
        assert!(issues.contains("derivative segments have no explicit account_model"));
        assert!(issues.contains("fee_rate is deprecated for live accounts"));
    }

    #[test]
    fn query_failures_preserve_permission_support_and_availability_semantics() {
        assert_eq!(
            classify_query_failure("provider rejected request: unauthorized"),
            AccountQueryCompleteness::Unauthorized
        );
        assert_eq!(
            classify_query_failure("this endpoint is not supported"),
            AccountQueryCompleteness::Unsupported
        );
        assert_eq!(
            classify_query_failure("connection timed out"),
            AccountQueryCompleteness::Unavailable
        );
    }

    #[test]
    fn account_balances_result_is_a_typed_account_query_result() {
        let value = serde_json::to_value(AccountBalancesResult {
            account_id: AccountId::new("paper-account").unwrap(),
            source: "local_registry".into(),
            mode: "standalone".into(),
            kind: "balances".into(),
            segments_requested: 1,
            segments_succeeded: 1,
            completeness: AccountQueryCompleteness::Complete,
            observed_at_unix_nanos: Some(1),
            balances: vec![AccountBalanceItem {
                segment: SegmentKey::new("spot").unwrap(),
                role: "wallet".into(),
                asset: Currency::new("USDT").unwrap(),
                total: "10000".parse::<DecimalParts>().unwrap(),
                available: Some("10000".parse::<DecimalParts>().unwrap()),
                locked: Some(DecimalParts::default()),
                borrowed: None,
                interest: None,
            }],
            collateral: vec![AccountBalanceItem {
                segment: SegmentKey::new("usd_m_futures").unwrap(),
                role: "collateral".into(),
                asset: Currency::new("USDT").unwrap(),
                total: "5000".parse::<DecimalParts>().unwrap(),
                available: Some("4000".parse::<DecimalParts>().unwrap()),
                locked: None,
                borrowed: None,
                interest: None,
            }],
            outcomes: vec![AccountSegmentQueryOutcome {
                segment: SegmentKey::new("spot").unwrap(),
                outcome: AccountQueryCompleteness::Complete,
                message: None,
            }],
            errors: Vec::new(),
        })
        .unwrap();

        assert_eq!(value["account_id"], "paper-account");
        assert_eq!(value["balances"][0]["asset"], "USDT");
        assert_eq!(value["balances"][0]["total"], "10000");
        assert_eq!(value["balances"][0]["locked"], "0");
        assert_eq!(value["collateral"][0]["role"], "collateral");
        assert_eq!(value["collateral"][0]["total"], "5000");
        assert!(value.get("initial_balances").is_none());
        assert!(value.get("credentials").is_none());
    }
}
