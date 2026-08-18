//! Async participant yield-product capabilities.

use std::future::Future;

use crate::domain::earn::{
    EarnActionQuery, EarnActionStatus, EarnPage, EarnPosition, EarnPositionsRequest, EarnProduct,
    EarnProductsRequest, EarnRateObservation, EarnRatesRequest, EarnRedeemRequest, EarnReward,
    EarnRewardsRequest, EarnSubmission, EarnSubscribeRequest, EarnSubscriptionPreview,
    EarnSubscriptionPreviewRequest,
};
use crate::{CommandResult, IntegrationError};

pub trait EarnProductQuery: Send {
    fn products(
        &mut self,
        request: &EarnProductsRequest,
    ) -> impl Future<Output = Result<EarnPage<EarnProduct>, IntegrationError>> + Send;

    fn positions(
        &mut self,
        request: &EarnPositionsRequest,
    ) -> impl Future<Output = Result<EarnPage<EarnPosition>, IntegrationError>> + Send;

    fn rewards(
        &mut self,
        request: &EarnRewardsRequest,
    ) -> impl Future<Output = Result<EarnPage<EarnReward>, IntegrationError>> + Send;

    fn rates(
        &mut self,
        request: &EarnRatesRequest,
    ) -> impl Future<Output = Result<EarnPage<EarnRateObservation>, IntegrationError>> + Send;

    fn subscription_preview(
        &mut self,
        request: &EarnSubscriptionPreviewRequest,
    ) -> impl Future<Output = Result<EarnSubscriptionPreview, IntegrationError>> + Send;
}

/// Submits capital to, or requests capital back from, a yield product.
///
/// A confirmed submission is not proof that capital is liquid again. Callers
/// must reconcile the action and then observe the destination Account segment.
pub trait EarnCommand: Send {
    fn subscribe(
        &mut self,
        request: &EarnSubscribeRequest,
    ) -> impl Future<Output = CommandResult<EarnSubmission>> + Send;

    fn redeem(
        &mut self,
        request: &EarnRedeemRequest,
    ) -> impl Future<Output = CommandResult<EarnSubmission>> + Send;
}

pub trait EarnActionStatusQuery: Send {
    fn action_status(
        &mut self,
        query: &EarnActionQuery,
    ) -> impl Future<Output = Result<Option<EarnActionStatus>, IntegrationError>> + Send;
}
