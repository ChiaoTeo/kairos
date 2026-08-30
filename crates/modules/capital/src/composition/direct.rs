//! Composition for one short-lived standalone Capital transfer.

use std::collections::BTreeMap;
use std::path::PathBuf;

use kairos_conflux::{
    AccountQuery, BinanceCoinMRestConnection, BinanceCredential, BinanceFundingRestConnection,
    BinanceMarginRestConnection, BinanceRestConfig, BinanceSpotRestConnection,
    BinanceUsdMRestConnection, ConfluxSystem, ConnectionKey, ExternalAccountIdentity,
    ExternalAccountSegment, ExternalAccountSnapshot, ExternalAccountStatus,
};
use kairos_credentials::CredentialStore;
use kairos_primitives::account::{AccountId, BrokerId, SegmentKey};
use kairos_primitives::decimal::Quantity;
use kairos_primitives::runtime::StrategyId;
use kairos_primitives::time::{Generation, Sequence, UnixNanos};
use kairos_workspace::Workspace;

use super::{
    CapitalConnectionAccount, CapitalIntegrationConnections,
    compose_capital_integration_connections, compose_persistent_capital_application,
    compose_persistent_capital_process,
};
use crate::application::{
    CliCapitalTransferApplication, ObserveCapitalFacts, StandaloneCapitalSegmentBinding,
    StandaloneCapitalTransferBinding, StandaloneCapitalTransferHistoryResult, UpdateCapitalPolicy,
    UpdateCapitalRoute, standalone_transfer_history,
};
use crate::domain::{
    CapitalFacts, CapitalGroupConfig, CapitalGroupId, CapitalGroupMember,
    CapitalMemberReadinessRole, CapitalPolicy, CapitalRouteId, CapitalRouteKind,
    CapitalSettlementClass, CapitalTransferRoute, FundingLocation,
};

pub async fn compose_standalone_capital_transfer(
    workspace: &Workspace,
    binding: StandaloneCapitalTransferBinding,
    observed_at: UnixNanos,
) -> Result<CliCapitalTransferApplication<CapitalIntegrationConnections>, Box<dyn std::error::Error>>
{
    binding.validate()?;
    validate_binding_credentials(workspace, &binding)?;
    if !binding.source.provider.eq_ignore_ascii_case("binance") {
        return Err(format!(
            "standalone Capital transfer is unavailable for provider {}",
            binding.source.provider
        )
        .into());
    }
    let source_snapshot = query_snapshot(workspace, &binding.source).await?;
    let destination_snapshot = query_snapshot(workspace, &binding.destination).await?;
    let source_location = binding.source_location()?;
    let destination_location = binding.destination_location()?;
    let group_id = direct_group_id(&binding)?;
    let config = group_config(&binding, group_id.clone())?;
    let state_path = direct_state_path(workspace, &group_id);
    let mut system = ConfluxSystem::new();
    let connections = compose_capital_integration_connections(
        &mut system,
        &workspace.existing_credentials_root()?,
        &binding.source.environment,
        connection_accounts(&binding),
    )?;
    let mut process = compose_persistent_capital_process(config, state_path, connections)?;
    let application = process.application_mut();
    let snapshot = application.snapshot();
    let source_facts = capital_facts(
        &snapshot.facts,
        source_location.clone(),
        &source_snapshot,
        &binding.asset,
        observed_at,
    )?;
    let destination_facts = capital_facts(
        &snapshot.facts,
        destination_location.clone(),
        &destination_snapshot,
        &binding.asset,
        observed_at,
    )?;
    let group_id = snapshot.capital_group_id;
    application.update_policy(UpdateCapitalPolicy {
        capital_group_id: group_id.clone(),
        policy: direct_policy(destination_location.clone()),
        updated_at: observed_at,
    })?;
    for facts in [source_facts, destination_facts] {
        application.observe_facts(ObserveCapitalFacts {
            capital_group_id: group_id.clone(),
            facts,
        })?;
    }
    application.update_route(UpdateCapitalRoute {
        capital_group_id: group_id,
        route: direct_route(source_location, destination_location),
        updated_at: observed_at,
    })?;
    CliCapitalTransferApplication::new(binding, process).map_err(Into::into)
}

fn validate_binding_credentials(
    workspace: &Workspace,
    binding: &StandaloneCapitalTransferBinding,
) -> Result<(), Box<dyn std::error::Error>> {
    let store = CredentialStore::load(workspace.existing_credentials_root()?)?;
    for (label, claimed) in [
        ("source", &binding.source),
        ("destination", &binding.destination),
    ]
    .into_iter()
    .chain(
        binding
            .controller
            .as_ref()
            .map(|value| ("controller", value)),
    ) {
        let credential_id = claimed
            .credential_id
            .as_deref()
            .ok_or_else(|| format!("standalone Capital {label} binding has no credential ID"))?;
        let actual = store
            .credentials
            .iter()
            .find(|credential| credential.credential_id == credential_id)
            .ok_or_else(|| {
                format!("standalone Capital {label} credential '{credential_id}' was not found")
            })?;
        if !actual.provider.eq_ignore_ascii_case(&claimed.provider) {
            return Err(format!(
                "standalone Capital {label} credential '{credential_id}' belongs to {}, not {}",
                actual.provider, claimed.provider
            )
            .into());
        }
        if !actual.role.eq_ignore_ascii_case(&claimed.credential_role) {
            return Err(format!(
                "standalone Capital {label} credential role changed; request a new Account binding"
            )
            .into());
        }
    }
    Ok(())
}

/// Open durable standalone transfer history without contacting the provider.
pub fn compose_standalone_capital_transfer_history(
    workspace: &Workspace,
    binding: StandaloneCapitalTransferBinding,
) -> Result<StandaloneCapitalTransferHistoryResult, Box<dyn std::error::Error>> {
    binding.validate()?;
    let group_id = direct_group_id(&binding)?;
    let config = group_config(&binding, group_id.clone())?;
    let application =
        compose_persistent_capital_application(config, direct_state_path(workspace, &group_id))?;
    Ok(standalone_transfer_history(&application))
}

fn group_config(
    binding: &StandaloneCapitalTransferBinding,
    capital_group_id: CapitalGroupId,
) -> Result<CapitalGroupConfig, String> {
    let mut members = BTreeMap::<(String, String), Vec<String>>::new();
    for value in [&binding.source, &binding.destination] {
        members
            .entry((value.broker.clone(), value.account_id.clone()))
            .or_default()
            .push(value.segment_key.clone());
    }
    Ok(CapitalGroupConfig {
        capital_group_id,
        strategy_id: StrategyId::new("standalone-capital-transfer")
            .map_err(|error| error.to_string())?,
        environment: binding.source.environment.clone(),
        membership_version: Generation::new(1),
        members: members
            .into_iter()
            .map(|((broker, account_id), mut segments)| {
                segments.sort();
                segments.dedup();
                Ok(CapitalGroupMember {
                    broker: BrokerId::new(broker).map_err(|error| error.to_string())?,
                    account_id: AccountId::new(account_id).map_err(|error| error.to_string())?,
                    permitted_segments: segments
                        .into_iter()
                        .map(SegmentKey::new)
                        .collect::<Result<_, _>>()
                        .map_err(|error| error.to_string())?,
                    readiness_role: CapitalMemberReadinessRole::Critical,
                })
            })
            .collect::<Result<_, String>>()?,
    })
}

fn connection_accounts(
    binding: &StandaloneCapitalTransferBinding,
) -> Vec<CapitalConnectionAccount> {
    let mut accounts = BTreeMap::<String, CapitalConnectionAccount>::new();
    for value in [&binding.source, &binding.destination] {
        let account =
            accounts
                .entry(value.account_id.clone())
                .or_insert_with(|| CapitalConnectionAccount {
                    account_id: value.account_id.clone(),
                    broker: value.broker.clone(),
                    integration_provider: value.provider.clone(),
                    environment: value.environment.clone(),
                    credential_id: value.credential_id.clone(),
                    capital_controller_account_id: value.capital_controller_account_id.clone(),
                    participant_account_ref: value.participant_account_ref.clone(),
                    account_socket: PathBuf::new(),
                    permitted_segments: Vec::new(),
                    segment_products: BTreeMap::new(),
                });
        if !account.permitted_segments.contains(&value.segment_key) {
            account.permitted_segments.push(value.segment_key.clone());
        }
        account
            .segment_products
            .insert(value.segment_key.clone(), value.provider_segment.clone());
    }
    if let Some(value) = &binding.controller {
        let account =
            accounts
                .entry(value.account_id.clone())
                .or_insert_with(|| CapitalConnectionAccount {
                    account_id: value.account_id.clone(),
                    broker: value.broker.clone(),
                    integration_provider: value.provider.clone(),
                    environment: value.environment.clone(),
                    credential_id: value.credential_id.clone(),
                    capital_controller_account_id: value.capital_controller_account_id.clone(),
                    participant_account_ref: value.participant_account_ref.clone(),
                    account_socket: PathBuf::new(),
                    permitted_segments: Vec::new(),
                    segment_products: BTreeMap::new(),
                });
        account.credential_id = value.credential_id.clone();
        if !account.permitted_segments.contains(&value.segment_key) {
            account.permitted_segments.push(value.segment_key.clone());
        }
        account
            .segment_products
            .insert(value.segment_key.clone(), value.provider_segment.clone());
    }
    accounts.into_values().collect()
}

fn direct_policy(destination: FundingLocation) -> CapitalPolicy {
    CapitalPolicy {
        destination,
        version: Generation::new(1),
        minimum: Quantity::ZERO,
        default_target: Quantity::ZERO,
        maximum: Quantity::new(i64::MAX, 0).expect("static Quantity"),
        stress_buffer: Quantity::ZERO,
        minimum_movement: Quantity::new(1, 18).expect("static Quantity"),
        hysteresis: Quantity::ZERO,
        deficit_dwell_nanos: 0,
        cooldown_nanos: 0,
        max_fact_age_nanos: 60_000_000_000,
    }
}

fn direct_route(source: FundingLocation, destination: FundingLocation) -> CapitalTransferRoute {
    let kind = if source.account_id == destination.account_id {
        CapitalRouteKind::InternalTransfer
    } else {
        CapitalRouteKind::AccountTransfer
    };
    CapitalTransferRoute {
        route_id: CapitalRouteId::new("standalone-direct-transfer").expect("static route id"),
        version: Generation::new(1),
        source,
        destination,
        kind,
        per_operation_limit: Quantity::new(i64::MAX, 0).expect("static Quantity"),
        daily_limit: Quantity::new(i64::MAX, 0).expect("static Quantity"),
        required_source_authority: kairos_primitives::capital::CapitalSourceAuthority::new(
            "standalone-explicit-confirmation",
        )
        .expect("standalone source authority is valid"),
        settlement_class: CapitalSettlementClass::ParticipantHistoryThenAccountObservation,
        enabled: true,
        earn_product_id: None,
        demand_guard_nanos: 0,
        allow_unknown_redemption_quota: false,
    }
}

fn capital_facts(
    existing: &[CapitalFacts],
    destination: FundingLocation,
    snapshot: &ExternalAccountSnapshot,
    asset: &str,
    observed_at: UnixNanos,
) -> Result<CapitalFacts, String> {
    let available = snapshot
        .balances
        .iter()
        .chain(snapshot.collateral.iter())
        .find(|balance| balance.asset_code.as_str().eq_ignore_ascii_case(asset))
        .map(|balance| balance.available.unwrap_or(balance.total))
        .ok_or_else(|| {
            format!(
                "Account returned no {asset} balance for {}",
                destination.segment
            )
        })?
        .format_fixed()?
        .parse::<Quantity>()
        .map_err(|error| error.to_string())?;
    let previous = existing
        .iter()
        .find(|facts| facts.destination == destination);
    let account_watermark = match previous {
        Some(facts) if facts.observed_available == available => facts.account_watermark,
        Some(facts) => Sequence::new(
            facts
                .account_watermark
                .get()
                .checked_add(1)
                .ok_or_else(|| "standalone Account watermark overflow".to_string())?,
        ),
        None => Sequence::new(1),
    };
    Ok(CapitalFacts {
        destination,
        observed_available: available,
        account_watermark,
        account_observed_at: observed_at,
        account_complete: snapshot.status == ExternalAccountStatus::Ready && !snapshot.partial,
        risk_capacity: Quantity::new(i64::MAX, 0).expect("static Quantity"),
        risk_policy_version: Generation::new(1),
        risk_watermark: Sequence::new(1),
        earn_holdings: Vec::new(),
    })
}

async fn query_snapshot(
    workspace: &Workspace,
    binding: &StandaloneCapitalSegmentBinding,
) -> Result<ExternalAccountSnapshot, Box<dyn std::error::Error>> {
    let credential_id = binding
        .credential_id
        .as_deref()
        .ok_or_else(|| format!("Account {} has no transfer credential", binding.account_id))?;
    let store = CredentialStore::load(workspace.existing_credentials_root()?)?;
    let credential = store
        .credentials
        .iter()
        .find(|credential| credential.credential_id == credential_id)
        .ok_or_else(|| format!("Capital credential '{credential_id}' was not found"))?;
    if !credential.provider.eq_ignore_ascii_case(&binding.provider) {
        return Err(format!(
            "Capital credential '{credential_id}' belongs to {}, not {}",
            credential.provider, binding.provider
        )
        .into());
    }
    let credential = BinanceCredential {
        principal_id: binding.remote_account_id.clone(),
        api_key: credential
            .api_key_value()
            .ok_or_else(|| format!("Capital credential '{credential_id}' has no API key"))?
            .into(),
        secret: credential
            .secret_value()
            .ok_or_else(|| format!("Capital credential '{credential_id}' has no API secret"))?
            .into(),
    };
    let config = BinanceRestConfig {
        environment: binding.environment.clone(),
        endpoint: binding.base_url.clone(),
        credential: Some(credential),
    };
    let key = ConnectionKey::new(format!(
        "capital.direct-facts.{}.{}",
        binding.account_id, binding.segment_key
    ))?;
    let segment = ExternalAccountSegment {
        identity: ExternalAccountIdentity::new(
            binding.provider.clone(),
            binding.account_id.clone(),
        )?,
        segment_key: SegmentKey::new(binding.segment_key.clone())?,
        environment: binding.environment.clone(),
        account_model: None,
    };
    let product = binding
        .provider_segment
        .trim()
        .to_ascii_lowercase()
        .replace('_', "-");
    let snapshot = match product.as_str() {
        "spot" => {
            BinanceSpotRestConnection::new(key, config)?
                .fetch_account(&segment)
                .await?
        },
        "funding" => {
            BinanceFundingRestConnection::new(key, config)?
                .fetch_account(&segment)
                .await?
        },
        "margin" | "cross-margin" | "isolated-margin" => {
            BinanceMarginRestConnection::new(key, config)?
                .fetch_account(&segment)
                .await?
        },
        "usd-m" | "usd-m-futures" => {
            BinanceUsdMRestConnection::new(key, config)?
                .fetch_account(&segment)
                .await?
        },
        "coin-m" | "coin-m-futures" => {
            BinanceCoinMRestConnection::new(key, config)?
                .fetch_account(&segment)
                .await?
        },
        value => {
            return Err(format!(
                "standalone Capital balance query is unavailable for Binance product {value}"
            )
            .into());
        },
    };
    Ok(snapshot)
}

fn direct_group_id(binding: &StandaloneCapitalTransferBinding) -> Result<CapitalGroupId, String> {
    let raw = format!(
        "standalone-{}-{}-{}-{}-{}",
        binding.source.account_id,
        binding.source.segment_key,
        binding.destination.account_id,
        binding.destination.segment_key,
        binding.asset.to_ascii_lowercase()
    );
    CapitalGroupId::new(safe_component(&raw)).map_err(|error| error.to_string())
}

fn direct_state_path(workspace: &Workspace, group_id: &CapitalGroupId) -> PathBuf {
    workspace
        .state_root()
        .join("capital")
        .join("standalone-transfer")
        .join(format!("{}.journal", safe_component(group_id.as_str())))
}

fn safe_component(value: &str) -> String {
    value
        .chars()
        .map(|character| {
            if character.is_ascii_alphanumeric() || matches!(character, '-' | '_' | '.') {
                character
            } else {
                '-'
            }
        })
        .collect()
}
