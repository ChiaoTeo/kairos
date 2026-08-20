use kairos_conflux::{
    AssetTransferCommand, AssetTransferQuery, AssetTransferRequest, AssetTransferState,
    AssetTransferStatusQuery, CommandOutcome, EarnActionKind, EarnActionQuery, EarnActionState,
    EarnActionStatusQuery, EarnCommand, EarnLiquidity, EarnProductQuery, EarnRedeemRequest,
    EarnRedemptionAmount, EarnRedemptionChannel, EarnSubscribeRequest, EarnSubscriptionEligibility,
    EarnSubscriptionPreviewRequest, ExternalAccountIdentity, ExternalAccountSegment,
    IntegrationError,
};
use kairos_primitives::time::UnixNanos;

use crate::application::{
    AuthorizeCapitalPlan, AuthorizeEarnSubscriptionPlan, BeginCapitalOperation, CapitalApplication,
    CapitalError, MarkCapitalDeliveryStarted, RecordCapitalParticipantStatus,
    RecordCapitalSubmission,
};
use crate::domain::{
    CapitalGroupId, CapitalOperation, CapitalOperationKind, CapitalOperationStatus,
    CapitalParticipantOperationState, CapitalPlan, CapitalPlanId, CapitalSubmissionOutcome,
};

/// Reusable runtime facade that drives one authoritative Capital application
/// through one participant-owned transfer capability.
pub struct CapitalTransferProcess<C> {
    application: CapitalApplication,
    connection: C,
    capital_group_id: CapitalGroupId,
    environment: String,
}

impl<C> CapitalTransferProcess<C>
where
    C: AssetTransferCommand + AssetTransferStatusQuery,
{
    pub(crate) fn new(
        application: CapitalApplication,
        connection: C,
        capital_group_id: CapitalGroupId,
        environment: String,
    ) -> Result<Self, CapitalProcessError> {
        if environment.is_empty() || environment.trim() != environment {
            return Err(CapitalProcessError::Invalid(
                "Capital process environment must be non-empty and trimmed".into(),
            ));
        }
        if application.snapshot().capital_group_id != capital_group_id {
            return Err(CapitalProcessError::Invalid(
                "Capital process group does not match its application".into(),
            ));
        }
        Ok(Self {
            application,
            connection,
            capital_group_id,
            environment,
        })
    }

    pub fn application(&self) -> &CapitalApplication {
        &self.application
    }

    pub fn application_mut(&mut self) -> &mut CapitalApplication {
        &mut self.application
    }

    /// Starts a transfer exactly once. A recovered or repeated call queries
    /// the already-fenced operation and never submits it again.
    pub async fn execute_transfer(
        &mut self,
        plan_id: CapitalPlanId,
        at: UnixNanos,
    ) -> Result<CapitalPlan, CapitalProcessError> {
        let operation = self.application.begin_operation(BeginCapitalOperation {
            capital_group_id: self.capital_group_id.clone(),
            plan_id: plan_id.clone(),
            at,
        })?;
        if operation.status != CapitalOperationStatus::Prepared {
            return self.reconcile_transfer(plan_id, at).await;
        }

        let operation = self
            .application
            .mark_delivery_started(MarkCapitalDeliveryStarted {
                capital_group_id: self.capital_group_id.clone(),
                plan_id: plan_id.clone(),
                at,
            })?;
        let plan = self.plan(&plan_id)?;
        let request = self.transfer_request(&plan, &operation)?;
        let outcome = self.connection.submit_transfer(&request).await;
        let command = match outcome {
            Ok(CommandOutcome::Confirmed(submission)) => RecordCapitalSubmission {
                capital_group_id: self.capital_group_id.clone(),
                plan_id,
                outcome: CapitalSubmissionOutcome::Confirmed,
                participant_operation_id: submission.participant_transfer_id,
                failure_reason: None,
                at,
            },
            Ok(CommandOutcome::Rejected(rejection)) => RecordCapitalSubmission {
                capital_group_id: self.capital_group_id.clone(),
                plan_id,
                outcome: CapitalSubmissionOutcome::Rejected,
                participant_operation_id: rejection.participant_request_id,
                failure_reason: Some(rejection.message),
                at,
            },
            Ok(CommandOutcome::Indeterminate(indeterminate)) => RecordCapitalSubmission {
                capital_group_id: self.capital_group_id.clone(),
                plan_id,
                outcome: CapitalSubmissionOutcome::Indeterminate,
                participant_operation_id: indeterminate.participant_request_id,
                failure_reason: Some(indeterminate.message),
                at,
            },
            Err(error) => RecordCapitalSubmission {
                capital_group_id: self.capital_group_id.clone(),
                plan_id,
                // The capability contract returns delivery ambiguity as a
                // CommandOutcome. An outer error therefore occurred before
                // the participant command was dispatched.
                outcome: CapitalSubmissionOutcome::Rejected,
                participant_operation_id: None,
                failure_reason: Some(error.to_string()),
                at,
            },
        };
        self.application
            .record_submission(command)
            .map_err(Into::into)
    }

    pub async fn reconcile_transfer(
        &mut self,
        plan_id: CapitalPlanId,
        at: UnixNanos,
    ) -> Result<CapitalPlan, CapitalProcessError> {
        let plan = self.plan(&plan_id)?;
        let operation = self.operation(&plan_id)?;
        if matches!(
            operation.status,
            CapitalOperationStatus::AwaitingAccountObservation
                | CapitalOperationStatus::Settled
                | CapitalOperationStatus::Rejected
                | CapitalOperationStatus::Failed
        ) {
            return Ok(plan);
        }
        if operation.status == CapitalOperationStatus::Prepared {
            return Err(CapitalProcessError::Invalid(
                "Capital operation has not crossed its durable delivery fence".into(),
            ));
        }
        let request = self.transfer_request(&plan, &operation)?;
        let query = AssetTransferQuery {
            request,
            participant_transfer_id: operation.participant_operation_id.clone(),
        };
        let Some(status) = self.connection.transfer_status(&query).await? else {
            if operation.status == CapitalOperationStatus::Dispatching {
                return self
                    .application
                    .record_submission(RecordCapitalSubmission {
                        capital_group_id: self.capital_group_id.clone(),
                        plan_id,
                        outcome: CapitalSubmissionOutcome::Indeterminate,
                        participant_operation_id: operation.participant_operation_id,
                        failure_reason: Some(
                            "participant history does not yet identify the dispatched transfer"
                                .into(),
                        ),
                        at,
                    })
                    .map_err(Into::into);
            }
            return Ok(plan);
        };
        let state = match status.state {
            AssetTransferState::Pending => CapitalParticipantOperationState::Pending,
            AssetTransferState::Succeeded => CapitalParticipantOperationState::Succeeded,
            AssetTransferState::Failed => CapitalParticipantOperationState::Failed,
            AssetTransferState::Cancelled => CapitalParticipantOperationState::Cancelled,
            AssetTransferState::Unknown => CapitalParticipantOperationState::Unknown,
        };
        self.application
            .record_participant_status(RecordCapitalParticipantStatus {
                capital_group_id: self.capital_group_id.clone(),
                plan_id,
                state,
                participant_operation_id: status.participant_transfer_id,
                participant_state: status.participant_state,
                failure_reason: status.failure_reason,
                at,
            })
            .map_err(Into::into)
    }

    fn plan(&self, plan_id: &CapitalPlanId) -> Result<CapitalPlan, CapitalProcessError> {
        self.application.plan(plan_id).cloned().ok_or_else(|| {
            CapitalProcessError::Invalid(format!("Capital plan '{plan_id}' was not found"))
        })
    }

    fn operation(&self, plan_id: &CapitalPlanId) -> Result<CapitalOperation, CapitalProcessError> {
        self.application
            .operation_for_plan(plan_id)
            .cloned()
            .ok_or_else(|| {
                CapitalProcessError::Invalid(format!(
                    "Capital plan '{plan_id}' has no transfer operation"
                ))
            })
    }

    fn transfer_request(
        &self,
        plan: &CapitalPlan,
        operation: &CapitalOperation,
    ) -> Result<AssetTransferRequest, CapitalProcessError> {
        let requested_at_unix_nanos = operation.dispatch_started_at.ok_or_else(|| {
            CapitalProcessError::Invalid(
                "Capital transfer operation has no durable dispatch timestamp".into(),
            )
        })?;
        Ok(AssetTransferRequest {
            idempotency_key: operation.idempotency_key.clone(),
            source: self.external_segment(&plan.source),
            destination: self.external_segment(&plan.destination),
            asset: plan.source.asset.clone(),
            amount: plan.amount,
            requested_at_unix_nanos,
            reason: Some(format!(
                "capital_group={} plan={} decision={}",
                self.capital_group_id, plan.plan_id, plan.rebalance_decision_id
            )),
        })
    }

    fn external_segment(
        &self,
        location: &crate::domain::FundingLocation,
    ) -> ExternalAccountSegment {
        ExternalAccountSegment {
            identity: ExternalAccountIdentity {
                broker: location.broker.to_string(),
                account_id: location.account_id.clone(),
            },
            segment_key: location.segment.clone(),
            environment: self.environment.clone(),
            account_model: None,
        }
    }
}

impl<C> CapitalTransferProcess<C>
where
    C: AssetTransferCommand + AssetTransferStatusQuery + EarnCommand + EarnActionStatusQuery,
{
    /// Queries principal-specific product terms through Conflux and then asks
    /// the Capital Actor to atomically reserve only the still-deployable cash.
    pub async fn authorize_earn_subscription_plan(
        &mut self,
        command: AuthorizeCapitalPlan,
    ) -> Result<Option<CapitalPlan>, CapitalProcessError>
    where
        C: EarnProductQuery,
    {
        let Some(candidate) = self
            .application
            .yield_candidate(&command.route_id, command.created_at)?
        else {
            return Ok(None);
        };
        let route = self
            .application
            .snapshot()
            .routes
            .into_iter()
            .find(|route| route.route_id == command.route_id)
            .ok_or_else(|| CapitalProcessError::Invalid("Capital route disappeared".into()))?;
        let preview = self
            .connection
            .subscription_preview(&EarnSubscriptionPreviewRequest {
                account: ExternalAccountIdentity {
                    broker: route.source.broker.to_string(),
                    account_id: route.source.account_id.clone(),
                },
                account_segment: self.external_segment(&route.source),
                asset: route.source.asset.clone(),
                product_id: candidate.product_id.clone(),
                amount: candidate.amount,
            })
            .await?;
        let immediate = preview.liquidity == EarnLiquidity::Immediate;
        let immediate_redemption = preview.redemption_options.iter().find(|option| {
            option.channel == EarnRedemptionChannel::Immediate
                && option.settlement_delay_seconds.unwrap_or(0) == 0
        });
        let plan = self
            .application
            .authorize_earn_subscription(AuthorizeEarnSubscriptionPlan {
                capital_group_id: command.capital_group_id,
                plan_id: command.plan_id,
                rebalance_decision_id: command.rebalance_decision_id,
                route_id: command.route_id,
                source_authority: command.source_authority,
                previewed_amount: preview.amount,
                preview_observed_at: preview.observed_at_unix_nanos,
                eligible: preview.eligibility == EarnSubscriptionEligibility::Eligible,
                immediately_redeemable: immediate && immediate_redemption.is_some(),
                redemption_quota_remaining: immediate_redemption
                    .and_then(|option| option.remaining_quota),
                created_at: command.created_at,
                expires_at: command.expires_at,
            })?;
        Ok(Some(plan))
    }

    /// Drives the next durable operation in a transfer-only or
    /// Earn-redemption-then-transfer plan.
    pub async fn execute_capital_plan(
        &mut self,
        plan_id: CapitalPlanId,
        at: UnixNanos,
    ) -> Result<CapitalPlan, CapitalProcessError> {
        let operation = self.application.begin_operation(BeginCapitalOperation {
            capital_group_id: self.capital_group_id.clone(),
            plan_id: plan_id.clone(),
            at,
        })?;
        match operation.kind {
            CapitalOperationKind::Transfer => self.execute_transfer(plan_id, at).await,
            CapitalOperationKind::EarnRedemption => {
                if operation.status == CapitalOperationStatus::Prepared {
                    self.submit_redemption(plan_id, at).await
                } else {
                    self.reconcile_redemption(plan_id, at).await
                }
            },
            CapitalOperationKind::EarnSubscription => {
                if operation.status == CapitalOperationStatus::Prepared {
                    self.submit_subscription(plan_id, at).await
                } else {
                    self.reconcile_subscription(plan_id, at).await
                }
            },
        }
    }

    /// Reconciles the operation already durably attached to a plan.
    ///
    /// This is intentionally narrower than [`Self::execute_capital_plan`]: it
    /// never creates the next operation, crosses a delivery fence, or submits
    /// a participant command. It is therefore safe for operator-triggered
    /// recovery and bounded shutdown draining.
    pub async fn reconcile_capital_plan(
        &mut self,
        plan_id: CapitalPlanId,
        at: UnixNanos,
    ) -> Result<CapitalPlan, CapitalProcessError> {
        let operation = self.operation(&plan_id)?;
        if operation.status == CapitalOperationStatus::Prepared {
            return Err(CapitalProcessError::Invalid(
                "a Prepared Capital operation has not been delivered and cannot be reconciled"
                    .into(),
            ));
        }
        match operation.kind {
            CapitalOperationKind::Transfer => self.reconcile_transfer(plan_id, at).await,
            CapitalOperationKind::EarnRedemption => self.reconcile_redemption(plan_id, at).await,
            CapitalOperationKind::EarnSubscription => {
                self.reconcile_subscription(plan_id, at).await
            },
        }
    }

    async fn submit_subscription(
        &mut self,
        plan_id: CapitalPlanId,
        at: UnixNanos,
    ) -> Result<CapitalPlan, CapitalProcessError> {
        let operation = self
            .application
            .mark_delivery_started(MarkCapitalDeliveryStarted {
                capital_group_id: self.capital_group_id.clone(),
                plan_id: plan_id.clone(),
                at,
            })?;
        let plan = self.plan(&plan_id)?;
        let product_id = plan.selected_earn_product_id.clone().ok_or_else(|| {
            CapitalProcessError::Invalid("Capital Earn plan has no selected product".into())
        })?;
        let request = EarnSubscribeRequest {
            account: ExternalAccountIdentity {
                broker: plan.source.broker.to_string(),
                account_id: plan.source.account_id.clone(),
            },
            account_segment: self.external_segment(&plan.source),
            asset: plan.source.asset.clone(),
            idempotency_key: operation.idempotency_key,
            product_id,
            amount: plan.amount,
            requested_at_unix_nanos: operation.dispatch_started_at.ok_or_else(|| {
                CapitalProcessError::Invalid(
                    "Capital subscription has no durable dispatch timestamp".into(),
                )
            })?,
        };
        let outcome = self.connection.subscribe(&request).await;
        let command = match outcome {
            Ok(CommandOutcome::Confirmed(submission)) => RecordCapitalSubmission {
                capital_group_id: self.capital_group_id.clone(),
                plan_id,
                outcome: CapitalSubmissionOutcome::Confirmed,
                participant_operation_id: submission.participant_action_id,
                failure_reason: None,
                at,
            },
            Ok(CommandOutcome::Rejected(rejection)) => RecordCapitalSubmission {
                capital_group_id: self.capital_group_id.clone(),
                plan_id,
                outcome: CapitalSubmissionOutcome::Rejected,
                participant_operation_id: rejection.participant_request_id,
                failure_reason: Some(rejection.message),
                at,
            },
            Ok(CommandOutcome::Indeterminate(indeterminate)) => RecordCapitalSubmission {
                capital_group_id: self.capital_group_id.clone(),
                plan_id,
                outcome: CapitalSubmissionOutcome::Indeterminate,
                participant_operation_id: indeterminate.participant_request_id,
                failure_reason: Some(indeterminate.message),
                at,
            },
            Err(error) => RecordCapitalSubmission {
                capital_group_id: self.capital_group_id.clone(),
                plan_id,
                outcome: CapitalSubmissionOutcome::Rejected,
                participant_operation_id: None,
                failure_reason: Some(error.to_string()),
                at,
            },
        };
        self.application
            .record_submission(command)
            .map_err(Into::into)
    }

    async fn reconcile_subscription(
        &mut self,
        plan_id: CapitalPlanId,
        at: UnixNanos,
    ) -> Result<CapitalPlan, CapitalProcessError> {
        let plan = self.plan(&plan_id)?;
        let operation = self.operation(&plan_id)?;
        if matches!(
            operation.status,
            CapitalOperationStatus::AwaitingAccountObservation
                | CapitalOperationStatus::Settled
                | CapitalOperationStatus::Rejected
                | CapitalOperationStatus::Failed
        ) {
            return Ok(plan);
        }
        let query = EarnActionQuery {
            account: ExternalAccountIdentity {
                broker: plan.source.broker.to_string(),
                account_id: plan.source.account_id.clone(),
            },
            account_segment: self.external_segment(&plan.source),
            asset: plan.source.asset.clone(),
            product_id: plan.selected_earn_product_id.clone().ok_or_else(|| {
                CapitalProcessError::Invalid("Capital Earn plan has no selected product".into())
            })?,
            idempotency_key: operation.idempotency_key,
            participant_action_id: operation.participant_operation_id.clone(),
            action: EarnActionKind::Subscribe,
        };
        let Some(status) = self.connection.action_status(&query).await? else {
            if operation.status == CapitalOperationStatus::Dispatching {
                return self
                    .application
                    .record_submission(RecordCapitalSubmission {
                        capital_group_id: self.capital_group_id.clone(),
                        plan_id,
                        outcome: CapitalSubmissionOutcome::Indeterminate,
                        participant_operation_id: operation.participant_operation_id,
                        failure_reason: Some(
                            "participant history does not yet identify the dispatched subscription"
                                .into(),
                        ),
                        at,
                    })
                    .map_err(Into::into);
            }
            return Ok(plan);
        };
        self.application
            .record_participant_status(RecordCapitalParticipantStatus {
                capital_group_id: self.capital_group_id.clone(),
                plan_id,
                state: match status.state {
                    EarnActionState::Pending => CapitalParticipantOperationState::Pending,
                    EarnActionState::Succeeded => CapitalParticipantOperationState::Succeeded,
                    EarnActionState::Failed => CapitalParticipantOperationState::Failed,
                    EarnActionState::Unknown => CapitalParticipantOperationState::Unknown,
                },
                participant_operation_id: status.participant_action_id,
                participant_state: status.participant_state,
                failure_reason: status.failure_reason,
                at,
            })
            .map_err(Into::into)
    }

    async fn submit_redemption(
        &mut self,
        plan_id: CapitalPlanId,
        at: UnixNanos,
    ) -> Result<CapitalPlan, CapitalProcessError> {
        let operation = self
            .application
            .mark_delivery_started(MarkCapitalDeliveryStarted {
                capital_group_id: self.capital_group_id.clone(),
                plan_id: plan_id.clone(),
                at,
            })?;
        let plan = self.plan(&plan_id)?;
        let product_id = plan.selected_earn_product_id.clone().ok_or_else(|| {
            CapitalProcessError::Invalid("Capital Earn plan has no selected product".into())
        })?;
        let request = EarnRedeemRequest {
            account: ExternalAccountIdentity {
                broker: plan.source.broker.to_string(),
                account_id: plan.source.account_id.clone(),
            },
            account_segment: self.external_segment(&plan.source),
            asset: plan.source.asset.clone(),
            idempotency_key: operation.idempotency_key,
            product_id,
            amount: EarnRedemptionAmount::Exact(plan.amount),
            destination: Some(self.external_segment(&plan.destination)),
            requested_at_unix_nanos: operation.dispatch_started_at.ok_or_else(|| {
                CapitalProcessError::Invalid(
                    "Capital redemption has no durable dispatch timestamp".into(),
                )
            })?,
        };
        let outcome = self.connection.redeem(&request).await;
        let command = match outcome {
            Ok(CommandOutcome::Confirmed(submission)) => RecordCapitalSubmission {
                capital_group_id: self.capital_group_id.clone(),
                plan_id,
                outcome: CapitalSubmissionOutcome::Confirmed,
                participant_operation_id: submission.participant_action_id,
                failure_reason: None,
                at,
            },
            Ok(CommandOutcome::Rejected(rejection)) => RecordCapitalSubmission {
                capital_group_id: self.capital_group_id.clone(),
                plan_id,
                outcome: CapitalSubmissionOutcome::Rejected,
                participant_operation_id: rejection.participant_request_id,
                failure_reason: Some(rejection.message),
                at,
            },
            Ok(CommandOutcome::Indeterminate(indeterminate)) => RecordCapitalSubmission {
                capital_group_id: self.capital_group_id.clone(),
                plan_id,
                outcome: CapitalSubmissionOutcome::Indeterminate,
                participant_operation_id: indeterminate.participant_request_id,
                failure_reason: Some(indeterminate.message),
                at,
            },
            Err(error) => RecordCapitalSubmission {
                capital_group_id: self.capital_group_id.clone(),
                plan_id,
                outcome: CapitalSubmissionOutcome::Rejected,
                participant_operation_id: None,
                failure_reason: Some(error.to_string()),
                at,
            },
        };
        self.application
            .record_submission(command)
            .map_err(Into::into)
    }

    async fn reconcile_redemption(
        &mut self,
        plan_id: CapitalPlanId,
        at: UnixNanos,
    ) -> Result<CapitalPlan, CapitalProcessError> {
        let plan = self.plan(&plan_id)?;
        let operation = self.operation(&plan_id)?;
        if matches!(
            operation.status,
            CapitalOperationStatus::AwaitingAccountObservation
                | CapitalOperationStatus::Settled
                | CapitalOperationStatus::Rejected
                | CapitalOperationStatus::Failed
        ) {
            return Ok(plan);
        }
        let query = EarnActionQuery {
            account: ExternalAccountIdentity {
                broker: plan.source.broker.to_string(),
                account_id: plan.source.account_id.clone(),
            },
            account_segment: self.external_segment(&plan.source),
            asset: plan.source.asset.clone(),
            product_id: plan.selected_earn_product_id.clone().ok_or_else(|| {
                CapitalProcessError::Invalid("Capital Earn plan has no selected product".into())
            })?,
            idempotency_key: operation.idempotency_key,
            participant_action_id: operation.participant_operation_id.clone(),
            action: EarnActionKind::Redeem,
        };
        let Some(status) = self.connection.action_status(&query).await? else {
            if operation.status == CapitalOperationStatus::Dispatching {
                return self
                    .application
                    .record_submission(RecordCapitalSubmission {
                        capital_group_id: self.capital_group_id.clone(),
                        plan_id,
                        outcome: CapitalSubmissionOutcome::Indeterminate,
                        participant_operation_id: operation.participant_operation_id,
                        failure_reason: Some(
                            "participant history does not yet identify the dispatched redemption"
                                .into(),
                        ),
                        at,
                    })
                    .map_err(Into::into);
            }
            return Ok(plan);
        };
        self.application
            .record_participant_status(RecordCapitalParticipantStatus {
                capital_group_id: self.capital_group_id.clone(),
                plan_id,
                state: match status.state {
                    EarnActionState::Pending => CapitalParticipantOperationState::Pending,
                    EarnActionState::Succeeded => CapitalParticipantOperationState::Succeeded,
                    EarnActionState::Failed => CapitalParticipantOperationState::Failed,
                    EarnActionState::Unknown => CapitalParticipantOperationState::Unknown,
                },
                participant_operation_id: status.participant_action_id,
                participant_state: status.participant_state,
                failure_reason: status.failure_reason,
                at,
            })
            .map_err(Into::into)
    }
}

#[derive(Debug, thiserror::Error)]
pub enum CapitalProcessError {
    #[error(transparent)]
    Capital(#[from] CapitalError),
    #[error("Capital connection failed: {0}")]
    Connection(String),
    #[error("invalid Capital process state: {0}")]
    Invalid(String),
}

impl From<IntegrationError> for CapitalProcessError {
    fn from(value: IntegrationError) -> Self {
        Self::Connection(value.to_string())
    }
}
