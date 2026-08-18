//! Synchronous account capabilities for dedicated blocking workers.

use std::time::Duration;

use crate::domain::account::{
    ExternalAccountEventEnvelope, ExternalAccountSegment, ExternalAccountSnapshot,
};
use crate::IntegrationError;
use crate::{
    ExternalAccountCredentialProfile, ExternalMarketProfile, ExternalMarketProfileRequest,
};

pub trait AccountQuery: Send {
    fn fetch_account(
        &mut self,
        segment: &ExternalAccountSegment,
    ) -> Result<ExternalAccountSnapshot, IntegrationError>;
}

pub trait AccountMarketProfileQuery: Send {
    fn fetch_market_profile(
        &mut self,
        request: &ExternalMarketProfileRequest,
    ) -> Result<ExternalMarketProfile, IntegrationError>;
}

pub trait AccountCredentialQuery: Send {
    fn inspect_credential(&mut self) -> Result<ExternalAccountCredentialProfile, IntegrationError>;
}

pub trait AccountStream: Send {
    fn next(
        &mut self,
        timeout: Duration,
    ) -> Result<Option<ExternalAccountEventEnvelope>, IntegrationError>;
}
