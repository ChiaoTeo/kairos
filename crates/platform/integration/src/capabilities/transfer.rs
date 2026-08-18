//! Async participant asset-transfer capabilities.

use std::future::Future;

use crate::domain::transfer::{
    AssetTransferQuery, AssetTransferRequest, AssetTransferStatus, AssetTransferSubmission,
};
use crate::{CommandResult, IntegrationError};

/// Moves an asset between two account locations exposed by one participant.
///
/// A confirmed submission only proves that the participant acknowledged the
/// request. Callers must use [`AssetTransferStatusQuery`] and Account facts to
/// establish final settlement.
pub trait AssetTransferCommand: Send {
    fn submit_transfer(
        &mut self,
        request: &AssetTransferRequest,
    ) -> impl Future<Output = CommandResult<AssetTransferSubmission>> + Send;
}

/// Reconciles a previously submitted transfer with participant-owned state.
pub trait AssetTransferStatusQuery: Send {
    fn transfer_status(
        &mut self,
        query: &AssetTransferQuery,
    ) -> impl Future<Output = Result<Option<AssetTransferStatus>, IntegrationError>> + Send;
}
