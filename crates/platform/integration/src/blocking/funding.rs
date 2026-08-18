//! Synchronous earn and transfer capabilities.

use crate::{
    CommandResult, EarnActionResult, EarnPosition, EarnProduct, EarnProductType, EarnRedeemRequest,
    EarnReward, EarnSubscribeRequest, IntegrationError, TransferRequest, TransferResult,
};

pub trait EarnQuery: Send {
    fn products(
        &mut self,
        asset: Option<&str>,
        product_type: Option<EarnProductType>,
    ) -> Result<Vec<EarnProduct>, IntegrationError>;
    fn positions(&mut self, asset: Option<&str>) -> Result<Vec<EarnPosition>, IntegrationError>;
    fn rewards(&mut self, asset: Option<&str>) -> Result<Vec<EarnReward>, IntegrationError>;
}

pub trait EarnCommand: Send {
    fn subscribe(&mut self, request: &EarnSubscribeRequest) -> CommandResult<EarnActionResult>;
    fn redeem(&mut self, request: &EarnRedeemRequest) -> CommandResult<EarnActionResult>;
}

pub trait FundsTransferCommand: Send {
    fn transfer(&mut self, request: &TransferRequest) -> CommandResult<TransferResult>;
}
