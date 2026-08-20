use std::collections::BTreeMap;
use std::future::Future;
use std::path::Path;

use kairos_primitives::{AccountId, Currency, IdempotencyKey, Quantity, SegmentKey, UnixNanos};

use crate::{
    AssetTransferCommand, AssetTransferQuery, AssetTransferRequest, AssetTransferState,
    AssetTransferStatus, AssetTransferStatusQuery, AssetTransferSubmission,
    BinanceCapitalRestConfig, BinanceCapitalRestConnection, BinanceCredential, BinanceRestConfig,
    BinanceSimpleEarnRestConnection, BinanceSubAccountCapitalRestConfig,
    BinanceSubAccountCapitalRestConnection, BinanceSubAccountIdentity, BinanceTransferAccount,
    CommandResult, ConnectionKey, CredentialStore, EarnActionKind, EarnActionQuery,
    EarnActionState, EarnActionStatus, EarnActionStatusQuery, EarnCommand, EarnLiquidity,
    EarnProductQuery, EarnRedeemRequest, EarnRedemptionAmount, EarnRedemptionChannel,
    EarnSubmission, EarnSubscribeRequest, EarnSubscriptionEligibility,
    EarnSubscriptionPreviewRequest, ExternalAccountIdentity, ExternalAccountSegment,
    IntegrationError,
};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapitalAccountIdentity {
    pub broker: String,
    pub account_id: AccountId,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapitalAccountSegment {
    pub identity: CapitalAccountIdentity,
    pub segment_key: SegmentKey,
    pub environment: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapitalTransferRequest {
    pub idempotency_key: IdempotencyKey,
    pub source: CapitalAccountSegment,
    pub destination: CapitalAccountSegment,
    pub asset: Currency,
    pub amount: Quantity,
    pub requested_at_unix_nanos: UnixNanos,
    pub reason: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapitalTransferSubmission {
    pub participant_transfer_id: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapitalTransferQuery {
    pub request: CapitalTransferRequest,
    pub participant_transfer_id: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CapitalTransferState {
    Pending,
    Succeeded,
    Failed,
    Cancelled,
    Unknown,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapitalTransferStatus {
    pub participant_transfer_id: Option<String>,
    pub state: CapitalTransferState,
    pub participant_state: Option<String>,
    pub failure_reason: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapitalCommandFailure {
    pub message: String,
    pub participant_request_id: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CapitalCommandOutcome<T> {
    Confirmed(T),
    Rejected(CapitalCommandFailure),
    Indeterminate(CapitalCommandFailure),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapitalEarnSubscribeRequest {
    pub account: CapitalAccountIdentity,
    pub idempotency_key: IdempotencyKey,
    pub product_id: String,
    pub amount: Quantity,
    pub requested_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapitalEarnRedeemRequest {
    pub account: CapitalAccountIdentity,
    pub idempotency_key: IdempotencyKey,
    pub product_id: String,
    pub amount: Quantity,
    pub requested_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapitalEarnSubmission {
    pub participant_action_id: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CapitalEarnActionKind {
    Subscribe,
    Redeem,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapitalEarnActionQuery {
    pub account: CapitalAccountIdentity,
    pub idempotency_key: IdempotencyKey,
    pub participant_action_id: Option<String>,
    pub action: CapitalEarnActionKind,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CapitalEarnActionState {
    Pending,
    Succeeded,
    Failed,
    Unknown,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapitalEarnActionStatus {
    pub participant_action_id: Option<String>,
    pub state: CapitalEarnActionState,
    pub participant_state: Option<String>,
    pub failure_reason: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapitalEarnSubscriptionPreviewRequest {
    pub account: CapitalAccountIdentity,
    pub product_id: String,
    pub amount: Quantity,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CapitalEarnSubscriptionEligibility {
    Eligible,
    Ineligible,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CapitalEarnLiquidity {
    Immediate,
    Delayed,
    Unknown,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapitalEarnRedemptionOption {
    pub immediate: bool,
    pub settlement_delay_seconds: Option<u64>,
    pub remaining_quota: Option<Quantity>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapitalEarnSubscriptionPreview {
    pub amount: Quantity,
    pub eligibility: CapitalEarnSubscriptionEligibility,
    pub liquidity: CapitalEarnLiquidity,
    pub redemption_options: Vec<CapitalEarnRedemptionOption>,
    pub observed_at_unix_nanos: UnixNanos,
}

#[derive(Debug, thiserror::Error)]
#[error("Capital connection failed: {message}")]
pub struct CapitalConnectionError {
    message: String,
}

impl From<IntegrationError> for CapitalConnectionError {
    fn from(value: IntegrationError) -> Self {
        Self {
            message: value.to_string(),
        }
    }
}

/// Non-secret account metadata used by Conflux to compose Capital transfer rails.
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
    pub permitted_segments: Vec<String>,
    pub segment_products: BTreeMap<String, String>,
}

/// Conflux-owned dispatcher for participant-neutral Capital transfer requests.
pub struct CapitalTransferConnections {
    binance: BTreeMap<String, BinanceCapitalRestConnection>,
    binance_subaccounts: BTreeMap<String, BinanceSubAccountCapitalRestConnection>,
    binance_earn: BTreeMap<String, BinanceSimpleEarnRestConnection>,
    account_controllers: BTreeMap<String, String>,
}

/// Conflux-owned Capital rail for participant-neutral asset movement.
///
/// Business modules depend on this boundary instead of naming Integration's
/// provider capability traits. Conflux remains responsible for selecting and
/// dispatching the concrete participant connection.
pub trait CapitalTransferConnection {
    fn submit_capital_transfer(
        &mut self,
        request: &CapitalTransferRequest,
    ) -> impl Future<
        Output = Result<CapitalCommandOutcome<CapitalTransferSubmission>, CapitalConnectionError>,
    > + Send;

    fn capital_transfer_status(
        &mut self,
        query: &CapitalTransferQuery,
    ) -> impl Future<Output = Result<Option<CapitalTransferStatus>, CapitalConnectionError>> + Send;
}

impl<T> CapitalTransferConnection for T
where
    T: AssetTransferCommand + AssetTransferStatusQuery,
{
    async fn submit_capital_transfer(
        &mut self,
        request: &CapitalTransferRequest,
    ) -> Result<CapitalCommandOutcome<CapitalTransferSubmission>, CapitalConnectionError> {
        let outcome = self
            .submit_transfer(&integration_transfer_request(request))
            .await?;
        Ok(match outcome {
            crate::CommandOutcome::Confirmed(value) => {
                CapitalCommandOutcome::Confirmed(CapitalTransferSubmission {
                    participant_transfer_id: value.participant_transfer_id,
                })
            },
            crate::CommandOutcome::Rejected(value) => {
                CapitalCommandOutcome::Rejected(CapitalCommandFailure {
                    message: value.message,
                    participant_request_id: value.participant_request_id,
                })
            },
            crate::CommandOutcome::Indeterminate(value) => {
                CapitalCommandOutcome::Indeterminate(CapitalCommandFailure {
                    message: value.message,
                    participant_request_id: value.participant_request_id,
                })
            },
        })
    }

    async fn capital_transfer_status(
        &mut self,
        query: &CapitalTransferQuery,
    ) -> Result<Option<CapitalTransferStatus>, CapitalConnectionError> {
        let status = self
            .transfer_status(&AssetTransferQuery {
                request: integration_transfer_request(&query.request),
                participant_transfer_id: query.participant_transfer_id.clone(),
            })
            .await?;
        Ok(status.map(|value| CapitalTransferStatus {
            participant_transfer_id: value.participant_transfer_id,
            state: match value.state {
                AssetTransferState::Pending => CapitalTransferState::Pending,
                AssetTransferState::Succeeded => CapitalTransferState::Succeeded,
                AssetTransferState::Failed => CapitalTransferState::Failed,
                AssetTransferState::Cancelled => CapitalTransferState::Cancelled,
                AssetTransferState::Unknown => CapitalTransferState::Unknown,
            },
            participant_state: value.participant_state,
            failure_reason: value.failure_reason,
        }))
    }
}

/// Conflux-owned Capital rail for liquid-yield subscription and redemption.
pub trait CapitalEarnConnection {
    fn subscribe_capital_earn(
        &mut self,
        request: &CapitalEarnSubscribeRequest,
    ) -> impl Future<
        Output = Result<CapitalCommandOutcome<CapitalEarnSubmission>, CapitalConnectionError>,
    > + Send;

    fn redeem_capital_earn(
        &mut self,
        request: &CapitalEarnRedeemRequest,
    ) -> impl Future<
        Output = Result<CapitalCommandOutcome<CapitalEarnSubmission>, CapitalConnectionError>,
    > + Send;

    fn capital_earn_action_status(
        &mut self,
        query: &CapitalEarnActionQuery,
    ) -> impl Future<Output = Result<Option<CapitalEarnActionStatus>, CapitalConnectionError>> + Send;
}

impl<T> CapitalEarnConnection for T
where
    T: EarnCommand + EarnActionStatusQuery,
{
    async fn subscribe_capital_earn(
        &mut self,
        request: &CapitalEarnSubscribeRequest,
    ) -> Result<CapitalCommandOutcome<CapitalEarnSubmission>, CapitalConnectionError> {
        let outcome = self
            .subscribe(&EarnSubscribeRequest {
                account: integration_account(&request.account),
                idempotency_key: request.idempotency_key.clone(),
                product_id: request.product_id.clone(),
                amount: request.amount,
                requested_at_unix_nanos: request.requested_at_unix_nanos,
            })
            .await?;
        Ok(map_earn_outcome(outcome))
    }

    async fn redeem_capital_earn(
        &mut self,
        request: &CapitalEarnRedeemRequest,
    ) -> Result<CapitalCommandOutcome<CapitalEarnSubmission>, CapitalConnectionError> {
        let outcome = self
            .redeem(&EarnRedeemRequest {
                account: integration_account(&request.account),
                idempotency_key: request.idempotency_key.clone(),
                product_id: request.product_id.clone(),
                amount: EarnRedemptionAmount::Exact(request.amount),
                destination: None,
                requested_at_unix_nanos: request.requested_at_unix_nanos,
            })
            .await?;
        Ok(map_earn_outcome(outcome))
    }

    async fn capital_earn_action_status(
        &mut self,
        query: &CapitalEarnActionQuery,
    ) -> Result<Option<CapitalEarnActionStatus>, CapitalConnectionError> {
        let status = self
            .action_status(&EarnActionQuery {
                account: integration_account(&query.account),
                idempotency_key: query.idempotency_key.clone(),
                participant_action_id: query.participant_action_id.clone(),
                action: match query.action {
                    CapitalEarnActionKind::Subscribe => EarnActionKind::Subscribe,
                    CapitalEarnActionKind::Redeem => EarnActionKind::Redeem,
                },
            })
            .await?;
        Ok(status.map(|value| CapitalEarnActionStatus {
            participant_action_id: value.participant_action_id,
            state: match value.state {
                EarnActionState::Pending => CapitalEarnActionState::Pending,
                EarnActionState::Succeeded => CapitalEarnActionState::Succeeded,
                EarnActionState::Failed => CapitalEarnActionState::Failed,
                EarnActionState::Unknown => CapitalEarnActionState::Unknown,
            },
            participant_state: value.participant_state,
            failure_reason: value.failure_reason,
        }))
    }
}

/// Conflux-owned Earn rail that can also establish principal-specific product
/// terms before Capital authorizes an idle-cash deployment.
pub trait CapitalEarnProductConnection: CapitalEarnConnection {
    fn preview_earn_subscription(
        &mut self,
        request: &CapitalEarnSubscriptionPreviewRequest,
    ) -> impl Future<Output = Result<CapitalEarnSubscriptionPreview, CapitalConnectionError>> + Send;
}

impl<T> CapitalEarnProductConnection for T
where
    T: CapitalEarnConnection + EarnProductQuery,
{
    fn preview_earn_subscription(
        &mut self,
        request: &CapitalEarnSubscriptionPreviewRequest,
    ) -> impl Future<Output = Result<CapitalEarnSubscriptionPreview, CapitalConnectionError>> + Send
    {
        async move {
            let preview = EarnProductQuery::subscription_preview(
                self,
                &EarnSubscriptionPreviewRequest {
                    account: integration_account(&request.account),
                    product_id: request.product_id.clone(),
                    amount: request.amount,
                },
            )
            .await?;
            Ok(map_earn_preview(preview))
        }
    }
}

impl EarnCommand for CapitalTransferConnections {
    async fn subscribe(&mut self, request: &EarnSubscribeRequest) -> CommandResult<EarnSubmission> {
        self.binance_earn
            .get_mut(request.account.account_id.as_str())
            .ok_or(IntegrationError::UnsupportedOperation)?
            .subscribe(request)
            .await
    }

    async fn redeem(&mut self, request: &EarnRedeemRequest) -> CommandResult<EarnSubmission> {
        self.binance_earn
            .get_mut(request.account.account_id.as_str())
            .ok_or(IntegrationError::UnsupportedOperation)?
            .redeem(request)
            .await
    }
}

impl EarnActionStatusQuery for CapitalTransferConnections {
    async fn action_status(
        &mut self,
        query: &EarnActionQuery,
    ) -> Result<Option<EarnActionStatus>, IntegrationError> {
        self.binance_earn
            .get_mut(query.account.account_id.as_str())
            .ok_or(IntegrationError::UnsupportedOperation)?
            .action_status(query)
            .await
    }
}

impl CapitalEarnProductConnection for CapitalTransferConnections {
    async fn preview_earn_subscription(
        &mut self,
        request: &CapitalEarnSubscriptionPreviewRequest,
    ) -> Result<CapitalEarnSubscriptionPreview, CapitalConnectionError> {
        let preview = self
            .binance_earn
            .get_mut(request.account.account_id.as_str())
            .ok_or(IntegrationError::UnsupportedOperation)?
            .subscription_preview(&EarnSubscriptionPreviewRequest {
                account: integration_account(&request.account),
                product_id: request.product_id.clone(),
                amount: request.amount,
            })
            .await?;
        Ok(map_earn_preview(preview))
    }
}

impl AssetTransferCommand for CapitalTransferConnections {
    async fn submit_transfer(
        &mut self,
        request: &AssetTransferRequest,
    ) -> CommandResult<AssetTransferSubmission> {
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

impl AssetTransferStatusQuery for CapitalTransferConnections {
    async fn transfer_status(
        &mut self,
        query: &AssetTransferQuery,
    ) -> Result<Option<AssetTransferStatus>, IntegrationError> {
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

/// Select and construct concrete participant connections behind Conflux.
pub fn compose_capital_transfer_connections(
    credential_config: &Path,
    launch_mode: &str,
    accounts: impl IntoIterator<Item = CapitalConnectionAccount>,
) -> Result<CapitalTransferConnections, String> {
    let credential_store = CredentialStore::load(credential_config)?;
    let accounts = accounts.into_iter().collect::<Vec<_>>();
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
    Ok(CapitalTransferConnections {
        binance,
        binance_subaccounts,
        binance_earn,
        account_controllers,
    })
}

fn provider(account: &CapitalConnectionAccount) -> &str {
    if account.integration_provider.is_empty() {
        account.broker.as_str()
    } else {
        account.integration_provider.as_str()
    }
}

fn integration_account(value: &CapitalAccountIdentity) -> ExternalAccountIdentity {
    ExternalAccountIdentity {
        broker: value.broker.clone(),
        account_id: value.account_id.clone(),
    }
}

fn integration_segment(value: &CapitalAccountSegment) -> ExternalAccountSegment {
    ExternalAccountSegment {
        identity: integration_account(&value.identity),
        segment_key: value.segment_key.clone(),
        environment: value.environment.clone(),
        account_model: None,
    }
}

fn integration_transfer_request(value: &CapitalTransferRequest) -> AssetTransferRequest {
    AssetTransferRequest {
        idempotency_key: value.idempotency_key.clone(),
        source: integration_segment(&value.source),
        destination: integration_segment(&value.destination),
        asset: value.asset.clone(),
        amount: value.amount,
        requested_at_unix_nanos: value.requested_at_unix_nanos,
        reason: value.reason.clone(),
    }
}

fn map_earn_outcome(
    value: crate::CommandOutcome<crate::EarnSubmission>,
) -> CapitalCommandOutcome<CapitalEarnSubmission> {
    match value {
        crate::CommandOutcome::Confirmed(value) => {
            CapitalCommandOutcome::Confirmed(CapitalEarnSubmission {
                participant_action_id: value.participant_action_id,
            })
        },
        crate::CommandOutcome::Rejected(value) => {
            CapitalCommandOutcome::Rejected(CapitalCommandFailure {
                message: value.message,
                participant_request_id: value.participant_request_id,
            })
        },
        crate::CommandOutcome::Indeterminate(value) => {
            CapitalCommandOutcome::Indeterminate(CapitalCommandFailure {
                message: value.message,
                participant_request_id: value.participant_request_id,
            })
        },
    }
}

fn map_earn_preview(value: crate::EarnSubscriptionPreview) -> CapitalEarnSubscriptionPreview {
    CapitalEarnSubscriptionPreview {
        amount: value.amount,
        eligibility: match value.eligibility {
            EarnSubscriptionEligibility::Eligible => CapitalEarnSubscriptionEligibility::Eligible,
            EarnSubscriptionEligibility::Ineligible { .. } => {
                CapitalEarnSubscriptionEligibility::Ineligible
            },
        },
        liquidity: match value.liquidity {
            EarnLiquidity::Immediate => CapitalEarnLiquidity::Immediate,
            EarnLiquidity::Notice { .. } | EarnLiquidity::FixedTerm { .. } => {
                CapitalEarnLiquidity::Delayed
            },
            EarnLiquidity::Unknown => CapitalEarnLiquidity::Unknown,
        },
        redemption_options: value
            .redemption_options
            .into_iter()
            .map(|option| CapitalEarnRedemptionOption {
                immediate: option.channel == EarnRedemptionChannel::Immediate,
                settlement_delay_seconds: option.settlement_delay_seconds,
                remaining_quota: option.remaining_quota,
            })
            .collect(),
        observed_at_unix_nanos: value.observed_at_unix_nanos,
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
