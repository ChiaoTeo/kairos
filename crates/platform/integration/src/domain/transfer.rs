//! Participant-neutral asset-transfer requests and external facts.

use kairos_primitives::decimal::Quantity;
use kairos_primitives::reference::Currency;
use kairos_primitives::runtime::IdempotencyKey;
use kairos_primitives::time::UnixNanos;

use crate::domain::account::ExternalAccountSegment;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AssetTransferRequest {
    /// Stable business-operation identity used to make retries safe.
    pub idempotency_key: IdempotencyKey,
    pub source: ExternalAccountSegment,
    pub destination: ExternalAccountSegment,
    pub asset: Currency,
    pub amount: Quantity,
    pub requested_at_unix_nanos: UnixNanos,
    pub reason: Option<String>,
}

impl AssetTransferRequest {
    pub fn validate(&self) -> Result<(), String> {
        if !self.amount.is_positive() {
            return Err("transfer amount must be positive".into());
        }
        if self.source == self.destination {
            return Err("transfer source and destination must differ".into());
        }
        if self.source.identity.broker != self.destination.identity.broker {
            return Err("asset transfer cannot cross participants".into());
        }
        if self.source.environment != self.destination.environment {
            return Err("asset transfer cannot cross environments".into());
        }
        Ok(())
    }
}

/// Participant acknowledgement of a transfer command, not final settlement.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AssetTransferSubmission {
    pub participant_transfer_id: Option<String>,
    pub acknowledged_at_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AssetTransferQuery {
    /// The original request is required because some participants require the
    /// route type for history queries and do not echo a client idempotency key.
    pub request: AssetTransferRequest,
    pub participant_transfer_id: Option<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AssetTransferState {
    Pending,
    Succeeded,
    Failed,
    Cancelled,
    /// A provider state that is preserved in `participant_state` but has no
    /// safe participant-neutral interpretation yet.
    Unknown,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AssetTransferStatus {
    pub idempotency_key: IdempotencyKey,
    pub participant_transfer_id: Option<String>,
    pub source: ExternalAccountSegment,
    pub destination: ExternalAccountSegment,
    pub asset: Currency,
    pub requested_amount: Quantity,
    pub settled_amount: Option<Quantity>,
    pub state: AssetTransferState,
    pub participant_state: Option<String>,
    pub updated_at_unix_nanos: Option<UnixNanos>,
    pub failure_reason: Option<String>,
}

#[cfg(test)]
mod tests {
    use kairos_primitives::account::{AccountId, SegmentKey};

    use super::*;
    use crate::{ExternalAccountIdentity, ExternalAccountSegment};

    fn segment(account: &str, segment: &str) -> ExternalAccountSegment {
        ExternalAccountSegment {
            identity: ExternalAccountIdentity {
                broker: "binance".into(),
                account_id: AccountId::new(account).unwrap(),
            },
            segment_key: SegmentKey::new(segment).unwrap(),
            environment: "live".into(),
            account_model: None,
        }
    }

    fn request(
        source: ExternalAccountSegment,
        destination: ExternalAccountSegment,
    ) -> AssetTransferRequest {
        AssetTransferRequest {
            idempotency_key: IdempotencyKey::new("capital-plan:1:transfer:1").unwrap(),
            source,
            destination,
            asset: Currency::new("USDT").unwrap(),
            amount: Quantity::positive(15_000, 0).unwrap(),
            requested_at_unix_nanos: UnixNanos::new(1),
            reason: Some("fund strategy margin".into()),
        }
    }

    #[test]
    fn allows_transfer_between_distinct_accounts_within_one_participant() {
        assert!(
            request(segment("master", "funding"), segment("strategy-a", "usd-m"))
                .validate()
                .is_ok()
        );
    }

    #[test]
    fn rejects_cross_participant_transfer_as_a_different_operation() {
        let mut destination = segment("strategy-a", "usd-m");
        destination.identity.broker = "okx".into();
        assert_eq!(
            request(segment("master", "funding"), destination).validate(),
            Err("asset transfer cannot cross participants".into())
        );
    }
}
