use std::collections::BTreeMap;
use std::path::PathBuf;

use kairos_conflux::{CredentialRecord, CredentialStore, ExternalAccountCredentialProfile};
use kairos_primitives::account::{AccountId, SegmentKey};
use kairos_primitives::decimal::DecimalParts;
use kairos_primitives::integration::ProviderId;
use kairos_primitives::reference::Currency;
use kairos_workspace::Workspace;
use serde::Serialize;

use crate::composition::account::{
    AccountOptions, default_rest_endpoint, inspect_account_credential,
};
use crate::composition::registry::{
    AccountBindingRecord, AccountCredentialBinding, AccountRegistry,
};
use crate::domain::AccountModel;

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
    pub provider: ProviderId,
    pub environment: String,
    pub segments: Vec<SegmentKey>,
    pub credential_id: Option<String>,
    pub status: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AccountBalancesResult {
    pub account_id: AccountId,
    pub source: String,
    pub mode: String,
    pub kind: String,
    pub balances: Vec<AccountBalanceItem>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AccountBalanceItem {
    pub segment: SegmentKey,
    pub asset: Currency,
    pub total: DecimalParts,
    pub available: DecimalParts,
    pub locked: DecimalParts,
}

impl TryFrom<&AccountBindingRecord> for AccountListItem {
    type Error = kairos_primitives::DomainTypeError;

    fn try_from(record: &AccountBindingRecord) -> Result<Self, Self::Error> {
        Ok(Self {
            account_id: AccountId::new(record.account_id.clone())?,
            alias: record.alias.clone(),
            provider: ProviderId::new(record.integration_provider.clone())?,
            environment: record.environment.clone(),
            segments: record
                .segments
                .iter()
                .cloned()
                .map(SegmentKey::new)
                .collect::<Result<_, _>>()?,
            credential_id: record.credential_id.clone(),
            status: record.status.clone(),
        })
    }
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

    pub fn local_balances(
        &self,
        account_id: &str,
        include_zero: bool,
    ) -> Result<AccountBalancesResult, Box<dyn std::error::Error>> {
        let account = self.local_query_account(account_id)?;
        let balances: Vec<_> = self
            .local_balances_for(account)?
            .into_iter()
            .filter(|value| include_zero || value.total.mantissa() != 0)
            .collect();
        Ok(AccountBalancesResult {
            account_id: AccountId::new(account.account_id.clone())?,
            source: "local_registry".into(),
            mode: "standalone".into(),
            kind: "balances".into(),
            balances,
        })
    }

    pub fn local_positions(
        &self,
        account_id: &str,
    ) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
        let account = self.local_query_account(account_id)?;
        Ok(serde_json::json!({
            "account_id": account.account_id,
            "source": "local_registry",
            "mode": "standalone",
            "kind": "positions",
            "positions": [],
        }))
    }

    pub fn local_open_orders(
        &self,
        account_id: &str,
    ) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
        let account = self.local_query_account(account_id)?;
        Ok(serde_json::json!({
            "account_id": account.account_id,
            "source": "local_registry",
            "mode": "standalone",
            "kind": "open_orders",
            "open_orders": [],
        }))
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
        Ok(serde_json::to_value(record)?)
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
        let issues: Vec<_> = self
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
        Ok(serde_json::json!({
            "accounts": selected_accounts,
            "issues": issues,
            "runtime": serde_json::Map::new(),
        }))
    }

    pub fn resolve_account_id(&self, value: &str) -> Result<String, Box<dyn std::error::Error>> {
        resolve_account_id(&self.registry, value)
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
        asset: Currency::new(asset.to_ascii_uppercase())?,
        total,
        available: total,
        locked: DecimalParts::default(),
    }))
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

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{AccountBalanceItem, AccountBalancesResult, AccountListItem, AccountListResult};
    use crate::composition::registry::AccountBindingRecord;
    use kairos_primitives::account::{AccountId, SegmentKey};
    use kairos_primitives::decimal::DecimalParts;
    use kairos_primitives::reference::Currency;

    #[test]
    fn account_list_result_exposes_only_list_semantics() {
        let record: AccountBindingRecord = serde_json::from_value(json!({
            "account_id": "main",
            "alias": "primary",
            "broker": "binance",
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
        assert_eq!(value["accounts"][0]["provider"], "binance");
        assert_eq!(value["accounts"][0]["segments"], json!(["spot"]));
        assert_eq!(value["count"], 1);
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
    fn account_balances_result_is_a_typed_account_query_result() {
        let value = serde_json::to_value(AccountBalancesResult {
            account_id: AccountId::new("paper-account").unwrap(),
            source: "local_registry".into(),
            mode: "standalone".into(),
            kind: "balances".into(),
            balances: vec![AccountBalanceItem {
                segment: SegmentKey::new("spot").unwrap(),
                asset: Currency::new("USDT").unwrap(),
                total: "10000".parse::<DecimalParts>().unwrap(),
                available: "10000".parse::<DecimalParts>().unwrap(),
                locked: DecimalParts::default(),
            }],
        })
        .unwrap();

        assert_eq!(value["account_id"], "paper-account");
        assert_eq!(value["balances"][0]["asset"], "USDT");
        assert_eq!(value["balances"][0]["total"], "10000");
        assert_eq!(value["balances"][0]["locked"], "0");
        assert!(value.get("initial_balances").is_none());
        assert!(value.get("credentials").is_none());
    }
}
