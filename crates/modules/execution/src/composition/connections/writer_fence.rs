//! Workspace lease fencing for live order-entry routes.

use super::*;

#[derive(Debug)]
pub struct ExecutionWriterFence {
    account_id: kairos_primitives::AccountId,
    segment_key: kairos_primitives::SegmentKey,
    lease: std::sync::Arc<kairos_workspace::WorkspaceFencedLease>,
}

impl ExecutionWriterFence {
    pub fn new(
        account_id: impl Into<String>,
        segment_key: impl Into<String>,
        lease: kairos_workspace::WorkspaceFencedLease,
    ) -> Result<Self, String> {
        Ok(Self {
            account_id: kairos_primitives::AccountId::new(account_id.into())
                .map_err(|error| error.to_string())?,
            segment_key: kairos_primitives::SegmentKey::new(segment_key.into())
                .map_err(|error| error.to_string())?,
            lease: std::sync::Arc::new(lease),
        })
    }

    pub fn token(&self) -> u64 {
        self.lease.token()
    }

    fn validates(&self, request: &OrderEntryRequest) -> bool {
        self.account_id == request.account_id && self.segment_key == request.segment_key
    }

    fn validate(&self) -> Result<(), IntegrationError> {
        self.lease.validate().map_err(|error| {
            IntegrationError::Authorization(format!(
                "Execution writer fencing rejected command: {error}"
            ))
        })
    }
}

impl ExecutionAsyncOrderEntryRoutes {
    pub fn install_writer_fences(
        &mut self,
        writer_fences: Vec<ExecutionWriterFence>,
    ) -> Result<(), String> {
        if writer_fences.is_empty() {
            return Err("live Execution requires at least one writer fence".into());
        }
        self.writer_fences = writer_fences;
        Ok(())
    }

    pub(super) fn validate_writer(
        &self,
        request: &OrderEntryRequest,
    ) -> Result<(), IntegrationError> {
        if self.writer_fences.is_empty() {
            // Offline/direct composition has no process lease boundary. The
            // production server must install fences before exposing a live gateway.
            return Ok(());
        }
        self.writer_fences
            .iter()
            .find(|fence| fence.validates(request))
            .ok_or_else(|| {
                IntegrationError::Authorization(format!(
                    "no Execution writer fence for account={}, segment={}",
                    request.account_id, request.segment_key
                ))
            })?
            .validate()
    }
}
