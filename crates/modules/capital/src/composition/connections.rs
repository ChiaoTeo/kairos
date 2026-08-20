use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use kairos_account_contract::{
    AccountContractClient, ContractError as AccountContractError, SimulatedCapitalMutation,
    SimulatedCapitalMutationKind, SimulatedCapitalMutationQuery, SimulatedCapitalMutationStatus,
};
use kairos_conflux::{
    AssetTransferCommand, AssetTransferQuery, AssetTransferRequest, AssetTransferState,
    AssetTransferStatus, AssetTransferStatusQuery, AssetTransferSubmission,
    BinanceCapitalRestConfig, BinanceCapitalRestConnection, BinanceCredential, BinanceRestConfig,
    BinanceSimpleEarnRestConnection, BinanceSubAccountCapitalRestConfig,
    BinanceSubAccountCapitalRestConnection, BinanceSubAccountIdentity, BinanceTransferAccount,
    CommandResult, ConnectionKey, CredentialStore, EarnActionKind, EarnActionQuery,
    EarnActionState, EarnActionStatus, EarnActionStatusQuery, EarnCommand, EarnLiquidity, EarnPage,
    EarnPosition, EarnPositionsRequest, EarnProduct, EarnProductQuery, EarnProductsRequest,
    EarnRateObservation, EarnRatesRequest, EarnRedeemRequest, EarnRedemptionAmount,
    EarnRedemptionChannel, EarnReward, EarnRewardsRequest, EarnSubmission, EarnSubscribeRequest,
    EarnSubscriptionEligibility, EarnSubscriptionPreviewRequest, ExternalAccountIdentity,
    IndeterminateCommand, IntegrationError, ParticipantRejection,
};
use kairos_primitives::account::{AccountId, SegmentKey};
use kairos_primitives::decimal::Quantity;
use kairos_primitives::reference::Currency;
use kairos_primitives::runtime::IdempotencyKey;
use kairos_primitives::time::UnixNanos;

/// Non-secret account metadata used by Capital composition to select transfer rails.
#[derive(Clone, Debug)]
pub struct CapitalConnectionAccount {
    pub account_id: String,
    pub broker: String,
    pub integration_provider: String,
    pub environment: String,
    pub credential_id: Option<String>,
    /// Account whose credential controls cross-Account participant transfers.
    /// The controller itself may omit this field; members name its AccountId.
    pub capital_controller_account_id: Option<String>,
    /// Provider-side non-secret identity within the controller's capital group.
    /// For Binance this is the subaccount email; the master leaves it empty.
    pub participant_account_ref: Option<String>,
    /// Instance-local Account control socket. Capital composition uses it only for the
    /// explicit paper/backtest rail; live participant rails never write
    /// Account-owned state through this endpoint.
    pub account_socket: PathBuf,
    pub permitted_segments: Vec<String>,
    pub segment_products: BTreeMap<String, String>,
}

#[derive(Clone, Debug)]
struct SimulatedCapitalAccount {
    socket: PathBuf,
    permitted_segments: Vec<SegmentKey>,
}

/// Capital composition dispatcher over participant-neutral Integration capabilities.
pub struct CapitalIntegrationConnections {
    binance: BTreeMap<String, BinanceCapitalRestConnection>,
    binance_subaccounts: BTreeMap<String, BinanceSubAccountCapitalRestConnection>,
    binance_earn: BTreeMap<String, BinanceSimpleEarnRestConnection>,
    account_controllers: BTreeMap<String, String>,
    simulated_accounts: BTreeMap<String, SimulatedCapitalAccount>,
}

impl CapitalIntegrationConnections {
    fn simulated_account(
        &self,
        identity: &ExternalAccountIdentity,
    ) -> Result<&SimulatedCapitalAccount, IntegrationError> {
        self.simulated_accounts
            .get(identity.account_id.as_str())
            .ok_or_else(|| {
                IntegrationError::Unavailable(format!(
                    "simulated Capital Account '{}' is unavailable",
                    identity.account_id
                ))
            })
    }

    async fn submit_simulated_transfer(
        &self,
        request: &AssetTransferRequest,
    ) -> CommandResult<AssetTransferSubmission> {
        let source = self.simulated_account(&request.source.identity)?.clone();
        let destination = self
            .simulated_account(&request.destination.identity)?
            .clone();
        require_simulated_segment(&source, &request.source.segment_key)?;
        require_simulated_segment(&destination, &request.destination.segment_key)?;
        let participant_request_id = simulated_participant_id(&request.idempotency_key);
        let debit_id = simulated_mutation_id(&request.idempotency_key, "source-debit")?;
        let credit_id = simulated_mutation_id(&request.idempotency_key, "destination-credit")?;
        let debit = SimulatedCapitalMutation {
            mutation_id: debit_id,
            segment_key: request.source.segment_key.clone(),
            asset: request.asset.clone(),
            amount: request.amount,
            kind: SimulatedCapitalMutationKind::DebitLiquid,
            product_id: None,
            occurred_at_unix_nanos: request.requested_at_unix_nanos,
        };
        if let Err(error) = apply_simulated_mutation(source.socket, debit).await? {
            return Ok(simulated_command_failure(
                error,
                participant_request_id,
                true,
            ));
        }
        let credit = SimulatedCapitalMutation {
            mutation_id: credit_id,
            segment_key: request.destination.segment_key.clone(),
            asset: request.asset.clone(),
            amount: request.amount,
            kind: SimulatedCapitalMutationKind::CreditLiquid,
            product_id: None,
            occurred_at_unix_nanos: request.requested_at_unix_nanos,
        };
        if let Err(error) = apply_simulated_mutation(destination.socket, credit).await? {
            let mut failure = IndeterminateCommand::may_have_been_sent(error.to_string());
            failure.participant_request_id = Some(participant_request_id);
            return Ok(kairos_conflux::CommandOutcome::Indeterminate(failure));
        }
        Ok(kairos_conflux::CommandOutcome::Confirmed(
            AssetTransferSubmission {
                participant_transfer_id: Some(participant_request_id),
                acknowledged_at_unix_nanos: Some(request.requested_at_unix_nanos),
            },
        ))
    }

    async fn simulated_transfer_status(
        &self,
        query: &AssetTransferQuery,
    ) -> Result<Option<AssetTransferStatus>, IntegrationError> {
        let source = self
            .simulated_account(&query.request.source.identity)?
            .clone();
        let destination = self
            .simulated_account(&query.request.destination.identity)?
            .clone();
        require_simulated_segment(&source, &query.request.source.segment_key)?;
        require_simulated_segment(&destination, &query.request.destination.segment_key)?;
        let debit = simulated_mutation_status(
            source.socket,
            SimulatedCapitalMutationQuery {
                mutation_id: simulated_mutation_id(&query.request.idempotency_key, "source-debit")?,
                segment_key: query.request.source.segment_key.clone(),
            },
        )
        .await?;
        let credit = simulated_mutation_status(
            destination.socket,
            SimulatedCapitalMutationQuery {
                mutation_id: simulated_mutation_id(
                    &query.request.idempotency_key,
                    "destination-credit",
                )?,
                segment_key: query.request.destination.segment_key.clone(),
            },
        )
        .await?;
        let state = if debit == SimulatedCapitalMutationStatus::Applied
            && credit == SimulatedCapitalMutationStatus::Applied
        {
            AssetTransferState::Succeeded
        } else {
            AssetTransferState::Unknown
        };
        Ok(Some(AssetTransferStatus {
            idempotency_key: query.request.idempotency_key.clone(),
            participant_transfer_id: query
                .participant_transfer_id
                .clone()
                .or_else(|| Some(simulated_participant_id(&query.request.idempotency_key))),
            source: query.request.source.clone(),
            destination: query.request.destination.clone(),
            asset: query.request.asset.clone(),
            requested_amount: query.request.amount,
            settled_amount: (state == AssetTransferState::Succeeded)
                .then_some(query.request.amount),
            state,
            participant_state: Some(
                match (debit, credit) {
                    (
                        SimulatedCapitalMutationStatus::Applied,
                        SimulatedCapitalMutationStatus::Applied,
                    ) => "applied",
                    (SimulatedCapitalMutationStatus::Applied, _) => "source_applied",
                    (_, SimulatedCapitalMutationStatus::Applied) => "destination_applied",
                    _ => "not_found",
                }
                .into(),
            ),
            updated_at_unix_nanos: None,
            failure_reason: None,
        }))
    }

    async fn submit_simulated_earn(
        &self,
        identity: &ExternalAccountIdentity,
        segment_key: &SegmentKey,
        idempotency_key: &IdempotencyKey,
        product_id: &str,
        asset: &Currency,
        amount: Quantity,
        occurred_at_unix_nanos: UnixNanos,
        kind: SimulatedCapitalMutationKind,
    ) -> CommandResult<EarnSubmission> {
        let account = self.simulated_account(identity)?.clone();
        require_simulated_segment(&account, segment_key)?;
        let suffix = match kind {
            SimulatedCapitalMutationKind::SubscribeEarn => "earn-subscribe",
            SimulatedCapitalMutationKind::RedeemEarn => "earn-redeem",
            _ => {
                return Err(IntegrationError::InvalidRequest(
                    "invalid simulated Earn mutation".into(),
                ));
            },
        };
        let participant_request_id = simulated_participant_id(idempotency_key);
        let mutation = SimulatedCapitalMutation {
            mutation_id: simulated_mutation_id(idempotency_key, suffix)?,
            segment_key: segment_key.clone(),
            asset: asset.clone(),
            amount,
            kind,
            product_id: Some(product_id.to_owned()),
            occurred_at_unix_nanos,
        };
        if let Err(error) = apply_simulated_mutation(account.socket, mutation).await? {
            return Ok(simulated_command_failure(
                error,
                participant_request_id,
                true,
            ));
        }
        Ok(kairos_conflux::CommandOutcome::Confirmed(EarnSubmission {
            participant_action_id: Some(participant_request_id),
            acknowledged_at_unix_nanos: Some(occurred_at_unix_nanos),
        }))
    }

    async fn simulated_earn_status(
        &self,
        query: &EarnActionQuery,
    ) -> Result<Option<EarnActionStatus>, IntegrationError> {
        let account = self.simulated_account(&query.account)?.clone();
        let suffix = match query.action {
            EarnActionKind::Subscribe => "earn-subscribe",
            EarnActionKind::Redeem => "earn-redeem",
        };
        let mutation_id = simulated_mutation_id(&query.idempotency_key, suffix)?;
        require_simulated_segment(&account, &query.account_segment.segment_key)?;
        let status = simulated_mutation_status(
            account.socket,
            SimulatedCapitalMutationQuery {
                mutation_id,
                segment_key: query.account_segment.segment_key.clone(),
            },
        )
        .await?;
        if status == SimulatedCapitalMutationStatus::Applied {
            return Ok(Some(EarnActionStatus {
                idempotency_key: query.idempotency_key.clone(),
                participant_action_id: query
                    .participant_action_id
                    .clone()
                    .or_else(|| Some(simulated_participant_id(&query.idempotency_key))),
                action: query.action,
                state: EarnActionState::Succeeded,
                participant_state: Some("applied".into()),
                updated_at_unix_nanos: None,
                failure_reason: None,
            }));
        }
        Ok(Some(EarnActionStatus {
            idempotency_key: query.idempotency_key.clone(),
            participant_action_id: query
                .participant_action_id
                .clone()
                .or_else(|| Some(simulated_participant_id(&query.idempotency_key))),
            action: query.action,
            state: EarnActionState::Unknown,
            participant_state: Some("not_found".into()),
            updated_at_unix_nanos: None,
            failure_reason: None,
        }))
    }
}

// Capital consumes the Integration-owned capabilities re-exported by
// Conflux; composition only selects their concrete live or simulated
// implementation.
impl EarnCommand for CapitalIntegrationConnections {
    async fn subscribe(&mut self, request: &EarnSubscribeRequest) -> CommandResult<EarnSubmission> {
        if !self.simulated_accounts.is_empty() {
            let outcome = self
                .submit_simulated_earn(
                    &request.account,
                    &request.account_segment.segment_key,
                    &request.idempotency_key,
                    &request.product_id,
                    &request.asset,
                    request.amount,
                    request.requested_at_unix_nanos,
                    SimulatedCapitalMutationKind::SubscribeEarn,
                )
                .await
                .map_err(|error| IntegrationError::Unavailable(error.to_string()))?;
            return Ok(outcome);
        }
        self.binance_earn
            .get_mut(request.account.account_id.as_str())
            .ok_or(IntegrationError::UnsupportedOperation)?
            .subscribe(request)
            .await
    }

    async fn redeem(&mut self, request: &EarnRedeemRequest) -> CommandResult<EarnSubmission> {
        if !self.simulated_accounts.is_empty() {
            let amount = match request.amount {
                EarnRedemptionAmount::Exact(amount) => amount,
                EarnRedemptionAmount::All => {
                    return Err(IntegrationError::UnsupportedOperation);
                },
            };
            let outcome = self
                .submit_simulated_earn(
                    &request.account,
                    &request.account_segment.segment_key,
                    &request.idempotency_key,
                    &request.product_id,
                    &request.asset,
                    amount,
                    request.requested_at_unix_nanos,
                    SimulatedCapitalMutationKind::RedeemEarn,
                )
                .await
                .map_err(|error| IntegrationError::Unavailable(error.to_string()))?;
            return Ok(outcome);
        }
        self.binance_earn
            .get_mut(request.account.account_id.as_str())
            .ok_or(IntegrationError::UnsupportedOperation)?
            .redeem(request)
            .await
    }
}

impl EarnActionStatusQuery for CapitalIntegrationConnections {
    async fn action_status(
        &mut self,
        query: &EarnActionQuery,
    ) -> Result<Option<EarnActionStatus>, IntegrationError> {
        if !self.simulated_accounts.is_empty() {
            return self.simulated_earn_status(query).await;
        }
        self.binance_earn
            .get_mut(query.account.account_id.as_str())
            .ok_or(IntegrationError::UnsupportedOperation)?
            .action_status(query)
            .await
    }
}

impl EarnProductQuery for CapitalIntegrationConnections {
    async fn products(
        &mut self,
        request: &EarnProductsRequest,
    ) -> Result<EarnPage<EarnProduct>, IntegrationError> {
        let connection = self
            .binance_earn
            .values_mut()
            .next()
            .ok_or(IntegrationError::UnsupportedOperation)?;
        connection.products(request).await
    }

    async fn positions(
        &mut self,
        request: &EarnPositionsRequest,
    ) -> Result<EarnPage<EarnPosition>, IntegrationError> {
        let connection = self
            .binance_earn
            .values_mut()
            .next()
            .ok_or(IntegrationError::UnsupportedOperation)?;
        connection.positions(request).await
    }

    async fn rewards(
        &mut self,
        request: &EarnRewardsRequest,
    ) -> Result<EarnPage<EarnReward>, IntegrationError> {
        let connection = self
            .binance_earn
            .values_mut()
            .next()
            .ok_or(IntegrationError::UnsupportedOperation)?;
        connection.rewards(request).await
    }

    async fn rates(
        &mut self,
        request: &EarnRatesRequest,
    ) -> Result<EarnPage<EarnRateObservation>, IntegrationError> {
        let connection = self
            .binance_earn
            .values_mut()
            .next()
            .ok_or(IntegrationError::UnsupportedOperation)?;
        connection.rates(request).await
    }

    async fn subscription_preview(
        &mut self,
        request: &EarnSubscriptionPreviewRequest,
    ) -> Result<kairos_conflux::EarnSubscriptionPreview, IntegrationError> {
        if !self.simulated_accounts.is_empty() {
            let account = self
                .simulated_accounts
                .get(request.account.account_id.as_str())
                .ok_or(IntegrationError::UnsupportedOperation)?;
            require_simulated_segment(account, &request.account_segment.segment_key)
                .map_err(|error| IntegrationError::InvalidRequest(error.to_string()))?;
            return Ok(kairos_conflux::EarnSubscriptionPreview {
                product_id: request.product_id.clone(),
                amount: request.amount,
                eligibility: EarnSubscriptionEligibility::Eligible,
                rate_components: Vec::new(),
                remaining_subscription_quota: Some(request.amount),
                liquidity: EarnLiquidity::Immediate,
                redemption_options: vec![kairos_conflux::EarnRedemptionOption {
                    channel: EarnRedemptionChannel::Immediate,
                    settlement_delay_seconds: Some(0),
                    remaining_quota: Some(request.amount),
                    forfeits_accrued_rewards: Some(false),
                }],
                observed_at_unix_nanos: now_unix_nanos()
                    .map_err(|error| IntegrationError::Unavailable(error.to_string()))?,
            });
        }
        self.binance_earn
            .get_mut(request.account.account_id.as_str())
            .ok_or(IntegrationError::UnsupportedOperation)?
            .subscription_preview(request)
            .await
    }
}

impl AssetTransferCommand for CapitalIntegrationConnections {
    async fn submit_transfer(
        &mut self,
        request: &AssetTransferRequest,
    ) -> CommandResult<AssetTransferSubmission> {
        if !self.simulated_accounts.is_empty() {
            return self.submit_simulated_transfer(request).await;
        }
        if request.source.identity.account_id == request.destination.identity.account_id {
            return self
                .binance
                .get_mut(request.source.identity.account_id.as_str())
                .ok_or(IntegrationError::UnsupportedOperation)?
                .submit_transfer(request)
                .await;
        }
        let controller = shared_controller(
            &self.account_controllers,
            request.source.identity.account_id.as_str(),
            request.destination.identity.account_id.as_str(),
        )?;
        self.binance_subaccounts
            .get_mut(controller)
            .ok_or(IntegrationError::UnsupportedOperation)?
            .submit_transfer(request)
            .await
    }
}

impl AssetTransferStatusQuery for CapitalIntegrationConnections {
    async fn transfer_status(
        &mut self,
        query: &AssetTransferQuery,
    ) -> Result<Option<AssetTransferStatus>, IntegrationError> {
        if !self.simulated_accounts.is_empty() {
            return self.simulated_transfer_status(query).await;
        }
        if query.request.source.identity.account_id == query.request.destination.identity.account_id
        {
            return self
                .binance
                .get_mut(query.request.source.identity.account_id.as_str())
                .ok_or(IntegrationError::UnsupportedOperation)?
                .transfer_status(query)
                .await;
        }
        let controller = shared_controller(
            &self.account_controllers,
            query.request.source.identity.account_id.as_str(),
            query.request.destination.identity.account_id.as_str(),
        )?;
        self.binance_subaccounts
            .get_mut(controller)
            .ok_or(IntegrationError::UnsupportedOperation)?
            .transfer_status(query)
            .await
    }
}

/// Select and construct the concrete Integration capabilities exposed to Capital.
pub fn compose_capital_integration_connections(
    credential_config: &Path,
    launch_mode: &str,
    accounts: impl IntoIterator<Item = CapitalConnectionAccount>,
) -> Result<CapitalIntegrationConnections, String> {
    let accounts = accounts.into_iter().collect::<Vec<_>>();
    if is_simulation_launch_mode(launch_mode) {
        let simulated_accounts = accounts
            .iter()
            .map(simulated_capital_account)
            .collect::<Result<BTreeMap<_, _>, _>>()?;
        return Ok(CapitalIntegrationConnections {
            binance: BTreeMap::new(),
            binance_subaccounts: BTreeMap::new(),
            binance_earn: BTreeMap::new(),
            account_controllers: BTreeMap::new(),
            simulated_accounts,
        });
    }
    let credential_store = CredentialStore::load(credential_config)?;
    let mut binance = BTreeMap::new();
    let mut binance_earn = BTreeMap::new();
    for account in &accounts {
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
        if let Some(credential) = credential.clone() {
            let earn = BinanceSimpleEarnRestConnection::new(
                ConnectionKey::new(format!("capital-earn:{}", account.account_id))?,
                BinanceRestConfig {
                    environment: launch_mode.to_owned(),
                    endpoint: endpoint.into(),
                    credential: Some(credential),
                },
            )
            .map_err(|error| error.to_string())?;
            binance_earn.insert(account.account_id.clone(), earn);
        }
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
        binance.insert(account.account_id.clone(), connection);
    }

    let accounts_by_id = accounts
        .iter()
        .map(|account| (account.account_id.as_str(), account))
        .collect::<BTreeMap<_, _>>();
    let mut controller_members = BTreeMap::<String, Vec<&CapitalConnectionAccount>>::new();
    for account in &accounts {
        if let Some(controller) = account.capital_controller_account_id.as_deref() {
            controller_members
                .entry(controller.to_owned())
                .or_default()
                .push(account);
        }
    }
    let mut binance_subaccounts = BTreeMap::new();
    let mut account_controllers = BTreeMap::new();
    for (controller_id, mut members) in controller_members {
        let controller = accounts_by_id
            .get(controller_id.as_str())
            .ok_or_else(|| format!("Capital controller Account '{controller_id}' was not found"))?;
        if !members
            .iter()
            .any(|account| account.account_id == controller_id)
        {
            members.push(controller);
        }
        if members.len() < 2 {
            continue;
        }
        let provider_name = provider(controller);
        if !provider_name.eq_ignore_ascii_case("binance") {
            return Err(format!(
                "Capital controller '{controller_id}' has no cross-Account rail for provider '{provider_name}'"
            ));
        }
        let credential_id = controller.credential_id.as_deref().ok_or_else(|| {
            format!("Capital controller Account '{controller_id}' requires a credential")
        })?;
        let credential_record = credential_store
            .credentials
            .iter()
            .find(|value| value.credential_id == credential_id)
            .ok_or_else(|| format!("Capital credential '{credential_id}' was not found"))?;
        let credential = BinanceCredential {
            principal_id: controller_id.clone(),
            api_key: credential_record
                .api_key_value()
                .ok_or_else(|| format!("Binance credential '{credential_id}' has no API key"))?
                .into(),
            secret: credential_record
                .secret_value()
                .ok_or_else(|| format!("Binance credential '{credential_id}' has no API secret"))?
                .into(),
        };
        let mut identities = BTreeMap::new();
        let mut segment_accounts = BTreeMap::new();
        for account in members {
            if !provider(account).eq_ignore_ascii_case(provider_name)
                || account.environment != controller.environment
            {
                return Err(format!(
                    "Capital group controlled by '{controller_id}' mixes providers or environments"
                ));
            }
            let account_id =
                AccountId::new(account.account_id.clone()).map_err(|error| error.to_string())?;
            let email = if account.account_id == controller_id {
                if account.participant_account_ref.is_some() {
                    return Err(format!(
                        "Capital controller Account '{controller_id}' cannot have participant_account_ref"
                    ));
                }
                None
            } else {
                Some(
                    account
                        .participant_account_ref
                        .as_deref()
                        .filter(|value| !value.trim().is_empty())
                        .ok_or_else(|| {
                            format!(
                                "Capital member Account '{}' requires participant_account_ref",
                                account.account_id
                            )
                        })?
                        .to_owned(),
                )
            };
            identities.insert(account_id.clone(), BinanceSubAccountIdentity { email });
            account_controllers.insert(account.account_id.clone(), controller_id.clone());
            for segment in &account.permitted_segments {
                let product = account
                    .segment_products
                    .get(segment)
                    .map(String::as_str)
                    .unwrap_or(segment);
                let transfer_account = binance_transfer_account(product)?;
                segment_accounts.insert(
                    (
                        account_id.clone(),
                        SegmentKey::new(segment).map_err(|error| error.to_string())?,
                    ),
                    transfer_account,
                );
            }
        }
        let endpoint = if controller.environment.eq_ignore_ascii_case("testnet") {
            "https://testnet.binance.vision"
        } else {
            "https://api.binance.com"
        };
        let connection = BinanceSubAccountCapitalRestConnection::new(
            ConnectionKey::new(format!("capital-subaccounts:{controller_id}"))?,
            BinanceSubAccountCapitalRestConfig {
                rest: BinanceRestConfig {
                    environment: launch_mode.to_owned(),
                    endpoint: endpoint.into(),
                    credential: Some(credential),
                },
                master_account_id: AccountId::new(controller_id.clone())
                    .map_err(|error| error.to_string())?,
                accounts: identities,
                segment_accounts,
            },
        )
        .map_err(|error| error.to_string())?;
        binance_subaccounts.insert(controller_id, connection);
    }
    Ok(CapitalIntegrationConnections {
        binance,
        binance_subaccounts,
        binance_earn,
        account_controllers,
        simulated_accounts: BTreeMap::new(),
    })
}

fn is_simulation_launch_mode(launch_mode: &str) -> bool {
    matches!(
        launch_mode.trim().to_ascii_lowercase().as_str(),
        "paper" | "backtest" | "simulated" | "simulation"
    )
}

fn simulated_capital_account(
    account: &CapitalConnectionAccount,
) -> Result<(String, SimulatedCapitalAccount), String> {
    if account.account_socket.as_os_str().is_empty() {
        return Err(format!(
            "simulated Capital Account '{}' requires an Account control socket",
            account.account_id
        ));
    }
    let permitted_segments = account
        .permitted_segments
        .iter()
        .map(|value| SegmentKey::new(value).map_err(|error| error.to_string()))
        .collect::<Result<Vec<_>, _>>()?;
    if permitted_segments.is_empty() {
        return Err(format!(
            "simulated Capital Account '{}' requires at least one permitted segment",
            account.account_id
        ));
    }
    Ok((
        account.account_id.clone(),
        SimulatedCapitalAccount {
            socket: account.account_socket.clone(),
            permitted_segments,
        },
    ))
}

fn require_simulated_segment(
    account: &SimulatedCapitalAccount,
    segment_key: &SegmentKey,
) -> Result<(), IntegrationError> {
    if account.permitted_segments.contains(segment_key) {
        Ok(())
    } else {
        Err(IntegrationError::InvalidRequest(format!(
            "segment '{segment_key}' is not permitted on the simulated Capital Account"
        )))
    }
}

fn simulated_mutation_id(
    idempotency_key: &IdempotencyKey,
    suffix: &str,
) -> Result<IdempotencyKey, IntegrationError> {
    IdempotencyKey::new(format!("{}:{suffix}", idempotency_key.as_str()))
        .map_err(|error| IntegrationError::InvalidRequest(error.to_string()))
}

fn simulated_participant_id(idempotency_key: &IdempotencyKey) -> String {
    format!("simulation:{}", idempotency_key.as_str())
}

async fn apply_simulated_mutation(
    socket: PathBuf,
    mutation: SimulatedCapitalMutation,
) -> Result<Result<(), AccountContractError>, IntegrationError> {
    tokio::task::spawn_blocking(move || {
        AccountContractClient::connect(socket)?.apply_simulated_capital_mutation(&mutation)
    })
    .await
    .map_err(|error| {
        IntegrationError::Unavailable(format!("simulated Account mutation task failed: {error}"))
    })
}

async fn simulated_mutation_status(
    socket: PathBuf,
    query: SimulatedCapitalMutationQuery,
) -> Result<SimulatedCapitalMutationStatus, IntegrationError> {
    tokio::task::spawn_blocking(move || {
        AccountContractClient::connect(socket)?.simulated_capital_mutation_status(&query)
    })
    .await
    .map_err(|error| {
        IntegrationError::Unavailable(format!("simulated Account status task failed: {error}"))
    })?
    .map(|response| response.status)
    .map_err(|error| IntegrationError::Unavailable(error.to_string()))
}

fn simulated_command_failure<T>(
    error: AccountContractError,
    participant_request_id: String,
    no_prior_effect: bool,
) -> kairos_conflux::CommandOutcome<T> {
    let is_definite = matches!(
        error,
        AccountContractError::Invalid(_) | AccountContractError::Unsupported(_)
    );
    if no_prior_effect && is_definite {
        kairos_conflux::CommandOutcome::Rejected(ParticipantRejection {
            code: None,
            message: error.to_string(),
            participant_request_id: Some(participant_request_id),
        })
    } else {
        let mut failure = IndeterminateCommand::may_have_been_sent(error.to_string());
        failure.participant_request_id = Some(participant_request_id);
        kairos_conflux::CommandOutcome::Indeterminate(failure)
    }
}

fn now_unix_nanos() -> Result<UnixNanos, IntegrationError> {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| IntegrationError::Unavailable(error.to_string()))?
        .as_nanos();
    let nanos = u64::try_from(nanos).map_err(|_| {
        IntegrationError::Unavailable("current time exceeds UnixNanos range".into())
    })?;
    Ok(UnixNanos::new(nanos))
}

fn provider(account: &CapitalConnectionAccount) -> &str {
    if account.integration_provider.is_empty() {
        account.broker.as_str()
    } else {
        account.integration_provider.as_str()
    }
}

fn shared_controller<'a>(
    controllers: &'a BTreeMap<String, String>,
    source: &str,
    destination: &str,
) -> Result<&'a str, IntegrationError> {
    let source_controller = controllers
        .get(source)
        .ok_or(IntegrationError::UnsupportedOperation)?;
    if controllers.get(destination) != Some(source_controller) {
        return Err(IntegrationError::UnsupportedOperation);
    }
    Ok(source_controller)
}

/// Validate a participant product name without exposing its concrete account enum.
pub fn validate_capital_transfer_product(provider: &str, value: &str) -> Result<(), String> {
    if !provider.eq_ignore_ascii_case("binance") {
        return Err(format!(
            "Capital transfer products are not implemented for provider '{provider}'"
        ));
    }
    binance_transfer_account(value).map(|_| ())
}

fn binance_transfer_account(value: &str) -> Result<BinanceTransferAccount, String> {
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

#[cfg(test)]
mod tests {
    use std::sync::{Arc, Mutex};

    use axum::extract::State;
    use axum::routing::post;
    use axum::{Json, Router};
    use kairos_account_contract::SimulatedCapitalMutationStatusResponse;

    use super::*;

    fn simulated_account() -> CapitalConnectionAccount {
        CapitalConnectionAccount {
            account_id: "strategy-main".into(),
            broker: "binance".into(),
            integration_provider: "binance".into(),
            environment: "paper".into(),
            credential_id: Some("must-not-be-read".into()),
            capital_controller_account_id: None,
            participant_account_ref: None,
            account_socket: PathBuf::from("/tmp/kairos-account-paper.sock"),
            permitted_segments: vec!["spot".into()],
            segment_products: BTreeMap::from([("spot".into(), "USDT001".into())]),
        }
    }

    fn account_with_socket(account_id: &str, socket: PathBuf) -> CapitalConnectionAccount {
        CapitalConnectionAccount {
            account_id: account_id.into(),
            account_socket: socket,
            ..simulated_account()
        }
    }

    #[derive(Clone, Default)]
    struct FakeAccountState {
        mutations: Arc<Mutex<Vec<SimulatedCapitalMutation>>>,
    }

    async fn apply_mutation(
        State(state): State<FakeAccountState>,
        Json(mutation): Json<SimulatedCapitalMutation>,
    ) -> Json<serde_json::Value> {
        let mut mutations = state.mutations.lock().unwrap();
        if !mutations
            .iter()
            .any(|value| value.mutation_id == mutation.mutation_id)
        {
            mutations.push(mutation);
        }
        Json(serde_json::json!({}))
    }

    async fn mutation_status(
        State(state): State<FakeAccountState>,
        Json(query): Json<SimulatedCapitalMutationQuery>,
    ) -> Json<SimulatedCapitalMutationStatusResponse> {
        let status = if state.mutations.lock().unwrap().iter().any(|value| {
            value.mutation_id == query.mutation_id && value.segment_key == query.segment_key
        }) {
            SimulatedCapitalMutationStatus::Applied
        } else {
            SimulatedCapitalMutationStatus::NotFound
        };
        Json(SimulatedCapitalMutationStatusResponse { status })
    }

    async fn serve_fake_account(
        socket: &Path,
        state: FakeAccountState,
    ) -> tokio::task::JoinHandle<()> {
        let listener = tokio::net::UnixListener::bind(socket).unwrap();
        let router = Router::new()
            .route("/v1/simulation/capital-mutations", post(apply_mutation))
            .route(
                "/v1/simulation/capital-mutations/status",
                post(mutation_status),
            )
            .with_state(state);
        tokio::spawn(async move {
            axum::serve(listener, router).await.unwrap();
        })
    }

    #[test]
    fn paper_capital_rail_does_not_load_credentials_or_construct_live_connections() {
        let connections = compose_capital_integration_connections(
            Path::new("/definitely/missing/credentials.toml"),
            "paper",
            [simulated_account()],
        )
        .unwrap();

        assert_eq!(connections.simulated_accounts.len(), 1);
        assert!(connections.binance.is_empty());
        assert!(connections.binance_subaccounts.is_empty());
        assert!(connections.binance_earn.is_empty());
    }

    #[test]
    fn live_capital_rail_still_requires_the_integration_credential_store() {
        let error = compose_capital_integration_connections(
            Path::new("/definitely/missing/credentials.toml"),
            "live",
            [simulated_account()],
        )
        .err()
        .expect("live composition must load credentials");

        assert!(error.contains("credential") || error.contains("No such file"));
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn simulated_transfer_dispatches_only_account_owned_mutations_and_reconciles_them() {
        let directory = tempfile::tempdir().unwrap();
        let source_socket = directory.path().join("source.sock");
        let destination_socket = directory.path().join("destination.sock");
        let source_state = FakeAccountState::default();
        let destination_state = FakeAccountState::default();
        let source_server = serve_fake_account(&source_socket, source_state.clone()).await;
        let destination_server =
            serve_fake_account(&destination_socket, destination_state.clone()).await;
        let mut connections = compose_capital_integration_connections(
            Path::new("/definitely/missing/credentials.toml"),
            "backtest",
            [
                account_with_socket("source", source_socket),
                account_with_socket("destination", destination_socket),
            ],
        )
        .unwrap();
        let request = AssetTransferRequest {
            idempotency_key: IdempotencyKey::new("plan-1:transfer").unwrap(),
            source: kairos_conflux::ExternalAccountSegment {
                identity: ExternalAccountIdentity {
                    broker: "binance".into(),
                    account_id: AccountId::new("source").unwrap(),
                },
                segment_key: SegmentKey::new("spot").unwrap(),
                environment: "backtest".into(),
                account_model: None,
            },
            destination: kairos_conflux::ExternalAccountSegment {
                identity: ExternalAccountIdentity {
                    broker: "binance".into(),
                    account_id: AccountId::new("destination").unwrap(),
                },
                segment_key: SegmentKey::new("spot").unwrap(),
                environment: "backtest".into(),
                account_model: None,
            },
            asset: Currency::new("USDT").unwrap(),
            amount: Quantity::new(25, 0).unwrap(),
            requested_at_unix_nanos: UnixNanos::new(10),
            reason: Some("test".into()),
        };

        let outcome = connections.submit_transfer(&request).await.unwrap();
        let participant_transfer_id = match outcome {
            kairos_conflux::CommandOutcome::Confirmed(value) => value.participant_transfer_id,
            other => panic!("unexpected simulated transfer outcome: {other:?}"),
        };
        let status = connections
            .transfer_status(&AssetTransferQuery {
                request,
                participant_transfer_id,
            })
            .await
            .unwrap()
            .unwrap();

        assert_eq!(status.state, AssetTransferState::Succeeded);
        assert_eq!(source_state.mutations.lock().unwrap().len(), 1);
        assert_eq!(destination_state.mutations.lock().unwrap().len(), 1);
        assert_eq!(
            source_state.mutations.lock().unwrap()[0].kind,
            SimulatedCapitalMutationKind::DebitLiquid
        );
        assert_eq!(
            destination_state.mutations.lock().unwrap()[0].kind,
            SimulatedCapitalMutationKind::CreditLiquid
        );
        source_server.abort();
        destination_server.abort();
    }
}
