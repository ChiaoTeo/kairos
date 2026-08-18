//! Async earn and transfer capabilities.

use std::future::Future;

use crate::domain::funding::{
    EarnActionResult, EarnPosition, EarnProduct, EarnProductType, EarnRedeemRequest, EarnReward,
    EarnSubscribeRequest, TransferRequest, TransferResult,
};
use crate::{CommandResult, IntegrationError};

pub trait EarnQuery: Send {
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
}

pub trait EarnCommand: Send {
    fn subscribe(
        &mut self,
        request: &EarnSubscribeRequest,
    ) -> impl Future<Output = CommandResult<EarnActionResult>> + Send;
    fn redeem(
        &mut self,
        request: &EarnRedeemRequest,
    ) -> impl Future<Output = CommandResult<EarnActionResult>> + Send;
}

pub trait FundsTransferCommand: Send {
    fn transfer(
        &mut self,
        request: &TransferRequest,
    ) -> impl Future<Output = CommandResult<TransferResult>> + Send;
}
