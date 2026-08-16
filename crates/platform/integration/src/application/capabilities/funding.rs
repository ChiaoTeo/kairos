//! Earn and transfer capabilities.

mod earn {
    use crate::application::{CommandResult, IntegrationError};
    use kairos_domain_types::{Currency, Quantity, Rate, UnixNanos};
    use std::future::Future;

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
        pub occurred_at_unix_millis: Option<UnixNanos>,
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

    /// Async-first Simple Earn capability. It deliberately has no lifecycle or
    /// runtime parameter: calling a method creates a Future which the owning
    /// business runtime may await or spawn.
    pub trait AsyncEarnConnection: Send {
        fn products(
            &mut self,
            asset: Option<&str>,
            product_type: Option<EarnProductType>,
        ) -> impl Future<Output = Result<Vec<EarnProduct>, IntegrationError>> + Send;
        fn positions(
            &mut self,
            asset: Option<&str>,
        ) -> impl Future<Output = Result<Vec<EarnPosition>, IntegrationError>> + Send;
        fn rewards(
            &mut self,
            asset: Option<&str>,
        ) -> impl Future<Output = Result<Vec<EarnReward>, IntegrationError>> + Send;
        fn subscribe(
            &mut self,
            request: &EarnSubscribeRequest,
        ) -> impl Future<Output = CommandResult<EarnActionResult>> + Send;
        fn redeem(
            &mut self,
            request: &EarnRedeemRequest,
        ) -> impl Future<Output = CommandResult<EarnActionResult>> + Send;
    }
}

pub use earn::*;

mod transfer {
    //! Provider-neutral account transfer capability.

    use crate::application::capabilities::account_facts::{
        ExternalAccountSegment as AccountSegment, ExternalDecimal as DecimalValue,
    };
    use crate::application::CommandResult;
    use std::future::Future;

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

    /// Async-first internal-wallet transfer capability. The Future runs on the
    /// caller's runtime; Integration does not own or accept a Tokio runtime.
    pub trait AsyncTransferConnection: Send {
        fn transfer(
            &mut self,
            request: &TransferRequest,
        ) -> impl Future<Output = CommandResult<TransferResult>> + Send;
    }
}

pub use transfer::*;
