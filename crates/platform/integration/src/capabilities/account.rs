//! Async account read, inspection, profile, and private-event capabilities.

use std::future::Future;
use std::task::{Context, Poll};

use crate::IntegrationError;
use crate::domain::account::{
    ExternalAccountCredentialProfile, ExternalAccountEventEnvelope, ExternalAccountSegment,
    ExternalAccountSnapshot, ExternalMarketProfile, ExternalMarketProfileRequest,
};

pub trait AccountQuery: Send {
    fn fetch_account(
        &mut self,
        segment: &ExternalAccountSegment,
    ) -> impl Future<Output = Result<ExternalAccountSnapshot, IntegrationError>> + Send;
}

pub trait AccountMarketProfileQuery: Send {
    fn fetch_market_profile(
        &mut self,
        request: &ExternalMarketProfileRequest,
    ) -> impl Future<Output = Result<ExternalMarketProfile, IntegrationError>> + Send;
}

pub trait AccountCredentialQuery: Send {
    fn inspect_credential(
        &mut self,
    ) -> impl Future<Output = Result<ExternalAccountCredentialProfile, IntegrationError>> + Send;
}

pub trait AccountStream: Send {
    fn poll_next(
        &mut self,
        cx: &mut Context<'_>,
    ) -> Poll<Result<ExternalAccountEventEnvelope, IntegrationError>>;
}
