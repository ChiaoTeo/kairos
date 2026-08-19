//! Synchronous account capabilities for dedicated blocking workers.

use crate::domain::account::{ExternalAccountSegment, ExternalAccountSnapshot};
use crate::{
    ExternalAccountCredentialProfile, ExternalMarketProfile, ExternalMarketProfileRequest,
    IntegrationError,
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
