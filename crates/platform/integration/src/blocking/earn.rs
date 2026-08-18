//! Synchronous participant yield-product capabilities.

use crate::{
    CommandResult, EarnActionQuery, EarnActionStatus, EarnPage, EarnPosition, EarnPositionsRequest,
    EarnProduct, EarnProductsRequest, EarnRateObservation, EarnRatesRequest, EarnRedeemRequest,
    EarnReward, EarnRewardsRequest, EarnSubmission, EarnSubscribeRequest, EarnSubscriptionPreview,
    EarnSubscriptionPreviewRequest, IntegrationError,
};

pub trait EarnProductQuery: Send {
    fn products(
        &mut self,
        request: &EarnProductsRequest,
    ) -> Result<EarnPage<EarnProduct>, IntegrationError>;
    fn positions(
        &mut self,
        request: &EarnPositionsRequest,
    ) -> Result<EarnPage<EarnPosition>, IntegrationError>;
    fn rewards(
        &mut self,
        request: &EarnRewardsRequest,
    ) -> Result<EarnPage<EarnReward>, IntegrationError>;
    fn rates(
        &mut self,
        request: &EarnRatesRequest,
    ) -> Result<EarnPage<EarnRateObservation>, IntegrationError>;
    fn subscription_preview(
        &mut self,
        request: &EarnSubscriptionPreviewRequest,
    ) -> Result<EarnSubscriptionPreview, IntegrationError>;
}

pub trait EarnCommand: Send {
    fn subscribe(&mut self, request: &EarnSubscribeRequest) -> CommandResult<EarnSubmission>;
    fn redeem(&mut self, request: &EarnRedeemRequest) -> CommandResult<EarnSubmission>;
}

pub trait EarnActionStatusQuery: Send {
    fn action_status(
        &mut self,
        query: &EarnActionQuery,
    ) -> Result<Option<EarnActionStatus>, IntegrationError>;
}
