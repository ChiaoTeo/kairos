use kairos_integration::{IntegrationError, OrderEntryRequest};

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

    pub(crate) fn validates(&self, request: &OrderEntryRequest) -> bool {
        self.account_id == request.account_id && self.segment_key == request.segment_key
    }

    pub(crate) fn validate(&self) -> Result<(), IntegrationError> {
        self.lease.validate().map_err(|error| {
            IntegrationError::Authorization(format!(
                "Execution writer fencing rejected command: {error}"
            ))
        })
    }
}
