//! Async observed fee schedule capability.

use std::future::Future;

use crate::{ExternalFeeSchedule, ExternalFeeScheduleRequest, IntegrationError};

pub trait FeeQuery: Send {
    fn fetch_fee_schedule(
        &mut self,
        request: &ExternalFeeScheduleRequest,
    ) -> impl Future<Output = Result<ExternalFeeSchedule, IntegrationError>> + Send;
}
