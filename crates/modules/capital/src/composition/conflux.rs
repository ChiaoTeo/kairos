use std::collections::BTreeMap;
use std::path::Path;

use kairos_conflux::{
    AssetTransferCommand, AssetTransferQuery, AssetTransferRequest, AssetTransferStatus,
    AssetTransferStatusQuery, AssetTransferSubmission, BinanceCapitalRestConfig,
    BinanceCapitalRestConnection, BinanceCredential, BinanceRestConfig, BinanceTransferAccount,
    CommandResult, ConnectionKey, CredentialStore, IntegrationError,
};
use kairos_primitives::SegmentKey;

#[derive(Clone, Debug)]
pub struct CapitalConnectionAccount {
    pub account_id: String,
    pub broker: String,
    pub integration_provider: String,
    pub environment: String,
    pub credential_id: Option<String>,
    pub permitted_segments: Vec<String>,
    pub segment_products: BTreeMap<String, String>,
}

pub struct ConfluxCapitalConnections {
    binance: BTreeMap<String, BinanceCapitalRestConnection>,
}

impl AssetTransferCommand for ConfluxCapitalConnections {
    async fn submit_transfer(
        &mut self,
        request: &AssetTransferRequest,
    ) -> CommandResult<AssetTransferSubmission> {
        self.binance
            .get_mut(&request.source.identity.account_id)
            .ok_or(IntegrationError::UnsupportedOperation)?
            .submit_transfer(request)
            .await
    }
}

impl AssetTransferStatusQuery for ConfluxCapitalConnections {
    async fn transfer_status(
        &mut self,
        query: &AssetTransferQuery,
    ) -> Result<Option<AssetTransferStatus>, IntegrationError> {
        self.binance
            .get_mut(&query.request.source.identity.account_id)
            .ok_or(IntegrationError::UnsupportedOperation)?
            .transfer_status(query)
            .await
    }
}

pub fn compose_capital_connections(
    credential_config: &Path,
    launch_mode: &str,
    accounts: impl IntoIterator<Item = CapitalConnectionAccount>,
) -> Result<ConfluxCapitalConnections, String> {
    let credential_store = CredentialStore::load(credential_config)?;
    let mut binance = BTreeMap::new();
    for account in accounts {
        let provider = if account.integration_provider.is_empty() {
            account.broker.as_str()
        } else {
            account.integration_provider.as_str()
        };
        if !provider.eq_ignore_ascii_case("binance") {
            continue;
        }
        let credential_record = account
            .credential_id
            .as_deref()
            .map(|credential_id| {
                credential_store
                    .credentials
                    .iter()
                    .find(|value| value.credential_id == credential_id)
                    .ok_or_else(|| format!("Capital credential '{credential_id}' was not found"))
            })
            .transpose()?;
        let credential = credential_record
            .map(|value| {
                let api_key = value.api_key_value().ok_or_else(|| {
                    format!(
                        "Binance credential '{}' has no API key",
                        value.credential_id
                    )
                })?;
                let secret = value.secret_value().ok_or_else(|| {
                    format!(
                        "Binance credential '{}' has no API secret",
                        value.credential_id
                    )
                })?;
                Ok::<_, String>(BinanceCredential {
                    principal_id: account.account_id.clone(),
                    api_key: api_key.into(),
                    secret: secret.into(),
                })
            })
            .transpose()?;
        let segment_accounts = account
            .permitted_segments
            .iter()
            .filter_map(|segment| {
                let product = account
                    .segment_products
                    .get(segment)
                    .map(String::as_str)
                    .unwrap_or(segment);
                binance_transfer_account(product)
                    .ok()
                    .map(|transfer_account| {
                        SegmentKey::new(segment)
                            .map(|segment| (segment, transfer_account))
                            .map_err(|error| error.to_string())
                    })
            })
            .collect::<Result<BTreeMap<_, _>, String>>()?;
        if segment_accounts.is_empty() {
            continue;
        }
        let endpoint = if account.environment.eq_ignore_ascii_case("testnet") {
            "https://testnet.binance.vision"
        } else {
            "https://api.binance.com"
        };
        let connection = BinanceCapitalRestConnection::new(
            ConnectionKey::new(format!("capital:{}", account.account_id))?,
            BinanceCapitalRestConfig {
                rest: BinanceRestConfig {
                    environment: launch_mode.to_owned(),
                    endpoint: endpoint.into(),
                    credential,
                },
                segment_accounts,
            },
        )
        .map_err(|error| error.to_string())?;
        binance.insert(account.account_id, connection);
    }
    Ok(ConfluxCapitalConnections { binance })
}

pub fn binance_transfer_account(value: &str) -> Result<BinanceTransferAccount, String> {
    match value.trim().to_ascii_lowercase().replace('_', "-").as_str() {
        "spot" => Ok(BinanceTransferAccount::Spot),
        "funding" => Ok(BinanceTransferAccount::Funding),
        "usd-m" | "usd-m-futures" | "um-futures" => Ok(BinanceTransferAccount::UsdMFutures),
        "coin-m" | "coin-m-futures" | "cm-futures" => Ok(BinanceTransferAccount::CoinMFutures),
        "cross-margin" | "margin" => Ok(BinanceTransferAccount::CrossMargin),
        other => Err(format!(
            "Binance Capital segment product '{other}' is not transferable"
        )),
    }
}
