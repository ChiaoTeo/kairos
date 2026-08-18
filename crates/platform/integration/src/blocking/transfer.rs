//! Synchronous participant asset-transfer capabilities.

use crate::{
    AssetTransferQuery, AssetTransferRequest, AssetTransferStatus, AssetTransferSubmission,
    CommandResult, IntegrationError,
};

pub trait AssetTransferCommand: Send {
    fn submit_transfer(
        &mut self,
        request: &AssetTransferRequest,
    ) -> CommandResult<AssetTransferSubmission>;
}

pub trait AssetTransferStatusQuery: Send {
    fn transfer_status(
        &mut self,
        query: &AssetTransferQuery,
    ) -> Result<Option<AssetTransferStatus>, IntegrationError>;
}
