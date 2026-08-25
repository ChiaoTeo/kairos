//! Short-lived, standalone Capital transfer use cases.

use kairos_conflux::{AssetTransferCommand, AssetTransferStatusQuery};
use kairos_primitives::account::{AccountId, BrokerId, SegmentKey};
use kairos_primitives::capital::CapitalPlanId;
use kairos_primitives::decimal::Quantity;
use kairos_primitives::reference::Currency;
use kairos_primitives::runtime::IdempotencyKey;
use kairos_primitives::time::UnixNanos;
use serde::{Deserialize, Serialize};

use super::{
    CapitalProcess, CapitalProcessError, ConfirmManualCapitalTransfer,
    ManualCapitalTransferPreview, ObserveCapitalSettlement, PreviewManualCapitalTransfer,
};
use crate::domain::{
    CapitalOperation, CapitalOperationStatus, CapitalPlan, CapitalPlanStatus, FundingLocation,
};

/// Account-owned, non-secret facts needed to compose one provider session.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct StandaloneCapitalSegmentBinding {
    pub account_id: String,
    pub remote_account_id: String,
    pub broker: String,
    pub provider: String,
    pub environment: String,
    pub segment_key: String,
    pub provider_segment: String,
    pub credential_id: Option<String>,
    pub credential_role: String,
    pub base_url: String,
    pub capital_controller_account_id: Option<String>,
    pub participant_account_ref: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct StandaloneCapitalTransferBinding {
    pub source: StandaloneCapitalSegmentBinding,
    pub destination: StandaloneCapitalSegmentBinding,
    #[serde(default)]
    pub controller: Option<StandaloneCapitalSegmentBinding>,
    pub asset: String,
}

impl StandaloneCapitalTransferBinding {
    pub fn validate(&self) -> Result<(), String> {
        for (label, binding) in [("source", &self.source), ("destination", &self.destination)] {
            if binding.account_id.trim().is_empty()
                || binding.broker.trim().is_empty()
                || binding.provider.trim().is_empty()
                || binding.environment.trim().is_empty()
                || binding.segment_key.trim().is_empty()
                || binding.provider_segment.trim().is_empty()
            {
                return Err(format!("standalone Capital {label} binding is incomplete"));
            }
            if !matches!(
                binding.credential_role.trim().to_ascii_lowercase().as_str(),
                "readonly" | "read" | "trade" | "trading" | "transfer" | "admin"
            ) {
                return Err(format!(
                    "standalone Capital {label} binding is not readable"
                ));
            }
        }
        if !self
            .source
            .provider
            .eq_ignore_ascii_case(&self.destination.provider)
        {
            return Err("standalone Capital transfer cannot cross providers".into());
        }
        if !self
            .source
            .broker
            .eq_ignore_ascii_case(&self.destination.broker)
        {
            return Err("standalone Capital transfer cannot cross brokers or exchanges".into());
        }
        if !self
            .source
            .environment
            .eq_ignore_ascii_case(&self.destination.environment)
        {
            return Err("standalone Capital transfer cannot cross environments".into());
        }
        if self.source.account_id == self.destination.account_id
            && self.source.segment_key == self.destination.segment_key
        {
            return Err("standalone Capital source and destination must differ".into());
        }
        if self.asset.trim().is_empty() {
            return Err("standalone Capital transfer asset is required".into());
        }
        if self.source.account_id != self.destination.account_id {
            let controller = self
                .source
                .capital_controller_account_id
                .as_deref()
                .or(self.destination.capital_controller_account_id.as_deref())
                .ok_or("cross-Account Capital transfer has no controller binding")?;
            for binding in [&self.source, &self.destination] {
                if binding.account_id != controller
                    && binding.capital_controller_account_id.as_deref() != Some(controller)
                {
                    return Err(
                        "cross-Account Capital transfer members do not share one controller".into(),
                    );
                }
                if binding.account_id != controller
                    && binding
                        .participant_account_ref
                        .as_deref()
                        .is_none_or(|value| value.trim().is_empty())
                {
                    return Err(format!(
                        "cross-Account Capital member '{}' has no participant account reference",
                        binding.account_id
                    ));
                }
            }
            let controller_binding = self
                .controller
                .as_ref()
                .ok_or("cross-Account Capital transfer requires a controller credential")?;
            if controller_binding.account_id != controller
                || !matches!(
                    controller_binding
                        .credential_role
                        .trim()
                        .to_ascii_lowercase()
                        .as_str(),
                    "transfer" | "admin"
                )
            {
                return Err("cross-Account Capital controller lacks transfer permission".into());
            }
            if !controller_binding
                .provider
                .eq_ignore_ascii_case(&self.source.provider)
                || !controller_binding
                    .environment
                    .eq_ignore_ascii_case(&self.source.environment)
                || !controller_binding
                    .broker
                    .eq_ignore_ascii_case(&self.source.broker)
            {
                return Err(
                    "cross-Account Capital controller is outside the transfer scope".into(),
                );
            }
        } else if !matches!(
            self.source
                .credential_role
                .trim()
                .to_ascii_lowercase()
                .as_str(),
            "transfer" | "admin"
        ) {
            return Err("standalone Capital source binding lacks transfer permission".into());
        }
        Ok(())
    }

    pub fn source_location(&self) -> Result<FundingLocation, String> {
        location(&self.source, &self.asset)
    }

    pub fn destination_location(&self) -> Result<FundingLocation, String> {
        location(&self.destination, &self.asset)
    }
}

#[derive(Clone, Debug)]
pub struct StandaloneCapitalTransferPreviewRequest {
    pub preview_id: String,
    pub plan_id: CapitalPlanId,
    pub idempotency_key: IdempotencyKey,
    pub amount: Quantity,
    pub source_authority: String,
    pub created_at: UnixNanos,
    pub expires_at: UnixNanos,
}

#[derive(Clone, Debug, Serialize)]
pub struct StandaloneCapitalTransferPreviewResult {
    pub owner: &'static str,
    pub mode: &'static str,
    pub scope: &'static str,
    pub status: &'static str,
    pub preview: ManualCapitalTransferPreview,
    pub source_available_after: Quantity,
    pub destination_available_after: Quantity,
}

#[derive(Clone, Debug, Serialize)]
pub struct StandaloneCapitalTransferResult {
    pub owner: &'static str,
    pub mode: &'static str,
    pub scope: &'static str,
    pub status: String,
    pub result_unknown: bool,
    pub plan: StandaloneCapitalPlanResult,
    pub operation: Option<StandaloneCapitalOperationResult>,
}

#[derive(Clone, Debug, Serialize)]
pub struct StandaloneCapitalTransferHistoryResult {
    pub owner: &'static str,
    pub mode: &'static str,
    pub scope: &'static str,
    pub transfers: Vec<StandaloneCapitalTransferHistoryItem>,
}

#[derive(Clone, Debug, Serialize)]
pub struct StandaloneCapitalTransferHistoryItem {
    pub plan: StandaloneCapitalPlanResult,
    pub operation: Option<StandaloneCapitalOperationResult>,
}

#[derive(Clone, Debug, Serialize)]
pub struct StandaloneCapitalPlanResult {
    pub plan_id: String,
    pub preview_id: String,
    pub idempotency_key: String,
    pub source: FundingLocation,
    pub destination: FundingLocation,
    pub amount: Quantity,
    pub status: String,
    pub created_at_unix_nanos: u64,
    pub expires_at_unix_nanos: u64,
}

#[derive(Clone, Debug, Serialize)]
pub struct StandaloneCapitalOperationResult {
    pub operation_id: String,
    pub status: String,
    pub participant_operation_id: Option<String>,
    pub participant_state: Option<String>,
    pub failure_reason: Option<String>,
    pub attempt_count: u32,
    pub updated_at_unix_nanos: u64,
}

pub struct CliCapitalTransferApplication<C> {
    binding: StandaloneCapitalTransferBinding,
    process: CapitalProcess<C>,
}

impl<C> CliCapitalTransferApplication<C>
where
    C: AssetTransferCommand + AssetTransferStatusQuery,
{
    pub(crate) fn new(
        binding: StandaloneCapitalTransferBinding,
        process: CapitalProcess<C>,
    ) -> Result<Self, String> {
        binding.validate()?;
        Ok(Self { binding, process })
    }

    pub fn preview(
        &self,
        request: StandaloneCapitalTransferPreviewRequest,
    ) -> Result<StandaloneCapitalTransferPreviewResult, CapitalProcessError> {
        let preview =
            self.process
                .application()
                .preview_manual_transfer(PreviewManualCapitalTransfer {
                    capital_group_id: self.process.application().snapshot().capital_group_id,
                    preview_id: request.preview_id,
                    plan_id: request.plan_id,
                    idempotency_key: request.idempotency_key,
                    source: self
                        .binding
                        .source_location()
                        .map_err(CapitalProcessError::Invalid)?,
                    destination: self
                        .binding
                        .destination_location()
                        .map_err(CapitalProcessError::Invalid)?,
                    amount: request.amount,
                    source_authority: request.source_authority,
                    created_at: request.created_at,
                    expires_at: request.expires_at,
                })?;
        let source_available_after = preview
            .source_observed_available
            .checked_sub(preview.amount)
            .map_err(|error| CapitalProcessError::Invalid(error.to_string()))?;
        let destination_available_after = preview
            .destination_observed_available
            .checked_add(preview.amount)
            .map_err(|error| CapitalProcessError::Invalid(error.to_string()))?;
        Ok(StandaloneCapitalTransferPreviewResult {
            owner: "capital",
            mode: "standalone",
            scope: "direct-provider",
            status: "confirmation_required",
            preview,
            source_available_after,
            destination_available_after,
        })
    }

    pub async fn confirm(
        &mut self,
        preview: ManualCapitalTransferPreview,
        confirmed_at: UnixNanos,
    ) -> Result<StandaloneCapitalTransferResult, CapitalProcessError> {
        let group_id = self.process.application().snapshot().capital_group_id;
        let plan = self.process.application_mut().confirm_manual_transfer(
            ConfirmManualCapitalTransfer {
                capital_group_id: group_id,
                preview,
                confirmed_at,
            },
        )?;
        let plan = self
            .process
            .execute_transfer(plan.plan_id.clone(), confirmed_at)
            .await?;
        Ok(result(&self.process, plan))
    }

    pub async fn status(
        &mut self,
        plan_id: CapitalPlanId,
        observed_at: UnixNanos,
    ) -> Result<StandaloneCapitalTransferResult, CapitalProcessError> {
        let plan = self
            .process
            .application()
            .plan(&plan_id)
            .cloned()
            .ok_or_else(|| {
                CapitalProcessError::Invalid(format!(
                    "standalone Capital transfer plan '{plan_id}' was not found"
                ))
            })?;
        let mut plan = if self
            .process
            .application()
            .operation_for_plan(&plan_id)
            .is_some()
        {
            self.process
                .reconcile_transfer(plan_id, observed_at)
                .await?
        } else {
            plan
        };
        if plan.status == CapitalPlanStatus::Reconciling
            && self
                .process
                .application()
                .operation_for_plan(&plan.plan_id)
                .is_some_and(|operation| {
                    operation.status == CapitalOperationStatus::AwaitingAccountObservation
                })
        {
            let snapshot = self.process.application().snapshot();
            let source = snapshot
                .facts
                .iter()
                .find(|facts| facts.destination == plan.source)
                .cloned();
            let destination = snapshot
                .facts
                .iter()
                .find(|facts| facts.destination == plan.destination)
                .cloned();
            if let (Some(source), Some(destination)) = (source, destination) {
                plan = self.process.application_mut().observe_settlement(
                    ObserveCapitalSettlement {
                        capital_group_id: snapshot.capital_group_id,
                        plan_id: plan.plan_id.clone(),
                        source,
                        destination,
                        observed_at,
                    },
                )?;
            }
        }
        Ok(result(&self.process, plan))
    }

    pub fn history(&self) -> StandaloneCapitalTransferHistoryResult {
        standalone_transfer_history(self.process.application())
    }
}

pub(crate) fn standalone_transfer_history(
    application: &super::CapitalApplication,
) -> StandaloneCapitalTransferHistoryResult {
    let snapshot = application.snapshot();
    let transfers = snapshot
        .plans
        .iter()
        .filter(|plan| plan.rebalance_decision_id.starts_with("manual-transfer:"))
        .map(|plan| StandaloneCapitalTransferHistoryItem {
            plan: plan_result(plan),
            operation: snapshot
                .operations
                .iter()
                .find(|operation| operation.plan_id == plan.plan_id)
                .map(operation_result),
        })
        .collect();
    StandaloneCapitalTransferHistoryResult {
        owner: "capital",
        mode: "standalone",
        scope: "direct-provider",
        transfers,
    }
}

fn result<C>(process: &CapitalProcess<C>, plan: CapitalPlan) -> StandaloneCapitalTransferResult
where
    C: AssetTransferCommand + AssetTransferStatusQuery,
{
    let operation = process
        .application()
        .operation_for_plan(&plan.plan_id)
        .map(operation_result);
    let result_unknown = operation.as_ref().is_some_and(|value| {
        matches!(
            value.status.as_str(),
            "dispatching" | "indeterminate" | "unknown"
        )
    });
    StandaloneCapitalTransferResult {
        owner: "capital",
        mode: "standalone",
        scope: "direct-provider",
        status: plan_status(&plan),
        result_unknown,
        plan: plan_result(&plan),
        operation,
    }
}

fn plan_result(plan: &CapitalPlan) -> StandaloneCapitalPlanResult {
    StandaloneCapitalPlanResult {
        plan_id: plan.plan_id.to_string(),
        preview_id: plan
            .rebalance_decision_id
            .strip_prefix("manual-transfer:")
            .unwrap_or(&plan.rebalance_decision_id)
            .to_owned(),
        idempotency_key: plan.idempotency_key.to_string(),
        source: plan.source.clone(),
        destination: plan.destination.clone(),
        amount: plan.amount,
        status: plan_status(plan),
        created_at_unix_nanos: plan.created_at.get(),
        expires_at_unix_nanos: plan.expires_at.get(),
    }
}

fn operation_result(operation: &CapitalOperation) -> StandaloneCapitalOperationResult {
    StandaloneCapitalOperationResult {
        operation_id: operation.operation_id.to_string(),
        status: operation_status(operation),
        participant_operation_id: operation.participant_operation_id.clone(),
        participant_state: operation.participant_state.clone(),
        failure_reason: operation.failure_reason.clone(),
        attempt_count: operation.attempt_count,
        updated_at_unix_nanos: operation.updated_at.get(),
    }
}

fn plan_status(plan: &CapitalPlan) -> String {
    match plan.status {
        CapitalPlanStatus::Authorized => "authorized",
        CapitalPlanStatus::Redeeming => "redeeming",
        CapitalPlanStatus::AwaitingRedemption => "awaiting_redemption",
        CapitalPlanStatus::Transferring => "transferring",
        CapitalPlanStatus::AwaitingTransfer => "awaiting_transfer",
        CapitalPlanStatus::Subscribing => "subscribing",
        CapitalPlanStatus::AwaitingSubscription => "awaiting_subscription",
        CapitalPlanStatus::Reconciling => "reconciling",
        CapitalPlanStatus::Available => "available",
        CapitalPlanStatus::Completed => "completed",
        CapitalPlanStatus::Indeterminate => "indeterminate",
        CapitalPlanStatus::Rejected => "rejected",
        CapitalPlanStatus::Expired => "expired",
        CapitalPlanStatus::Failed => "failed",
    }
    .into()
}

fn operation_status(operation: &CapitalOperation) -> String {
    match operation.status {
        CapitalOperationStatus::Prepared => "prepared",
        CapitalOperationStatus::Dispatching => "dispatching",
        CapitalOperationStatus::AwaitingParticipant => "awaiting_participant",
        CapitalOperationStatus::Indeterminate => "indeterminate",
        CapitalOperationStatus::AwaitingAccountObservation => "awaiting_account_observation",
        CapitalOperationStatus::Settled => "settled",
        CapitalOperationStatus::Expired => "expired",
        CapitalOperationStatus::Rejected => "rejected",
        CapitalOperationStatus::Failed => "failed",
    }
    .into()
}

fn location(
    binding: &StandaloneCapitalSegmentBinding,
    asset: &str,
) -> Result<FundingLocation, String> {
    Ok(FundingLocation {
        broker: BrokerId::new(binding.broker.clone()).map_err(|error| error.to_string())?,
        account_id: AccountId::new(binding.account_id.clone())
            .map_err(|error| error.to_string())?,
        segment: SegmentKey::new(binding.segment_key.clone()).map_err(|error| error.to_string())?,
        asset: Currency::new(asset.to_owned()).map_err(|error| error.to_string())?,
    })
}

#[cfg(test)]
mod tests {
    use super::{StandaloneCapitalSegmentBinding, StandaloneCapitalTransferBinding};

    fn segment(account_id: &str, segment_key: &str, role: &str) -> StandaloneCapitalSegmentBinding {
        StandaloneCapitalSegmentBinding {
            account_id: account_id.into(),
            remote_account_id: account_id.into(),
            broker: "binance".into(),
            provider: "binance".into(),
            environment: "live".into(),
            segment_key: segment_key.into(),
            provider_segment: segment_key.into(),
            credential_id: Some(format!("{account_id}-{role}")),
            credential_role: role.into(),
            base_url: "https://api.binance.com".into(),
            capital_controller_account_id: None,
            participant_account_ref: None,
        }
    }

    #[test]
    fn same_account_transfer_requires_one_transfer_capable_source_binding() {
        let mut binding = StandaloneCapitalTransferBinding {
            source: segment("main", "funding", "readonly"),
            destination: segment("main", "spot", "readonly"),
            controller: None,
            asset: "USDT".into(),
        };
        assert!(
            binding
                .validate()
                .unwrap_err()
                .contains("transfer permission")
        );
        binding.source.credential_role = "transfer".into();
        assert!(binding.validate().is_ok());
    }

    #[test]
    fn cross_account_transfer_requires_one_shared_transfer_controller() {
        let mut source = segment("sub-a", "spot", "readonly");
        source.capital_controller_account_id = Some("main".into());
        source.participant_account_ref = Some("sub-a@example.com".into());
        let mut destination = segment("sub-b", "spot", "readonly");
        destination.capital_controller_account_id = Some("main".into());
        destination.participant_account_ref = Some("sub-b@example.com".into());
        let mut binding = StandaloneCapitalTransferBinding {
            source,
            destination,
            controller: None,
            asset: "USDT".into(),
        };
        assert!(
            binding
                .validate()
                .unwrap_err()
                .contains("controller credential")
        );
        binding.controller = Some(segment("main", "spot", "transfer"));
        assert!(binding.validate().is_ok());
    }
}
