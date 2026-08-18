//! Earn and transfer requests, results, and facts.

mod earn {
    use kairos_primitives::{Currency, Quantity, Rate, UnixNanos};

    #[derive(Clone, Copy, Debug, Eq, PartialEq)]
    pub enum EarnProductType {
        Flexible,
        Locked,
    }

    #[derive(Clone, Debug, Eq, PartialEq)]
    pub struct EarnProduct {
        pub product_id: String,
        pub asset: Currency,
        pub product_type: EarnProductType,
        pub annual_rate: Rate,
        pub min_amount: Quantity,
        pub max_amount: Quantity,
        pub status: String,
        pub duration_days: Option<u32>,
    }

    #[derive(Clone, Debug, Eq, PartialEq)]
    pub struct EarnPosition {
        pub product_id: String,
        pub asset: Currency,
        pub amount: Quantity,
        pub rewards: Quantity,
        pub annual_rate: Rate,
        pub status: String,
        pub updated_at_unix_millis: Option<UnixNanos>,
    }

    #[derive(Clone, Debug, Eq, PartialEq)]
    pub struct EarnReward {
        pub asset: Currency,
        pub amount: Quantity,
        pub product_id: Option<String>,
        pub occurred_at_unix_nanos: Option<UnixNanos>,
    }

    #[derive(Clone, Debug, Eq, PartialEq)]
    pub struct EarnSubscribeRequest {
        pub product_id: String,
        pub product_type: EarnProductType,
        pub amount: Quantity,
        pub auto_renew: Option<bool>,
    }

    #[derive(Clone, Debug, Eq, PartialEq)]
    pub struct EarnRedeemRequest {
        pub product_id: String,
        pub product_type: EarnProductType,
        pub amount: Option<Quantity>,
        pub destination_account: Option<String>,
    }

    #[derive(Clone, Debug, Eq, PartialEq)]
    pub struct EarnActionResult {
        pub accepted: bool,
        pub action_id: Option<String>,
        pub status: String,
        pub reason: String,
    }
}

pub use earn::*;

mod transfer {
    //! Participant-neutral account transfer capability.

    use crate::domain::account::{
        ExternalAccountSegment as AccountSegment, ExternalDecimal as DecimalValue,
    };

    #[derive(Clone, Debug, Eq, PartialEq)]
    pub struct TransferRequest {
        pub source: AccountSegment,
        pub destination: AccountSegment,
        pub asset: String,
        pub amount: DecimalValue,
    }

    impl TransferRequest {
        pub fn validate(&self) -> Result<(), String> {
            if self.asset.trim().is_empty() {
                return Err("transfer asset is required".into());
            }
            if self.amount.mantissa <= 0 {
                return Err("transfer amount must be positive".into());
            }
            if self.source.identity != self.destination.identity {
                return Err(
                    "source and destination must belong to the same external account".into(),
                );
            }
            Ok(())
        }
    }

    #[derive(Clone, Debug, Eq, PartialEq)]
    pub struct TransferResult {
        pub accepted: bool,
        pub reference_id: Option<String>,
        pub reason: String,
    }
}

pub use transfer::*;
