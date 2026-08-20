use kairos_primitives::integration::ProviderId;
use kairos_primitives::reference::InstrumentId;
use kairos_protocol::control::jsonrpc::{RpcResult, rpc};

use super::{
    ReferenceHealthResponse, ReferenceMutationResponse, ReferenceOptionCoverageResponse,
    ReferencePublishResponse, ReferenceRefreshResponse, ReferenceSourceStatusResponse,
    UpsertAssetRequest, UpsertInstrumentRequest, UpsertListingRequest,
};

#[rpc(client, server, namespace = "reference")]
pub trait ReferenceControlRpc {
    #[method(name = "health")]
    async fn health(&self) -> RpcResult<ReferenceHealthResponse>;

    #[method(name = "refresh")]
    async fn refresh(&self, source_id: Option<ProviderId>) -> RpcResult<ReferenceRefreshResponse>;

    #[method(name = "publish")]
    async fn publish(&self) -> RpcResult<ReferencePublishResponse>;

    #[method(name = "pause_source")]
    async fn pause_source(&self, source_id: ProviderId)
    -> RpcResult<ReferenceSourceStatusResponse>;

    #[method(name = "resume_source")]
    async fn resume_source(
        &self,
        source_id: ProviderId,
    ) -> RpcResult<ReferenceSourceStatusResponse>;

    #[method(name = "add_option_coverage")]
    async fn add_option_coverage(
        &self,
        underlying: InstrumentId,
    ) -> RpcResult<ReferenceOptionCoverageResponse>;

    #[method(name = "remove_option_coverage")]
    async fn remove_option_coverage(
        &self,
        underlying: InstrumentId,
    ) -> RpcResult<ReferenceOptionCoverageResponse>;

    #[method(name = "upsert_asset")]
    async fn upsert_asset(
        &self,
        request: UpsertAssetRequest,
    ) -> RpcResult<ReferenceMutationResponse>;

    #[method(name = "upsert_instrument")]
    async fn upsert_instrument(
        &self,
        request: UpsertInstrumentRequest,
    ) -> RpcResult<ReferenceMutationResponse>;

    #[method(name = "upsert_listing")]
    async fn upsert_listing(
        &self,
        request: UpsertListingRequest,
    ) -> RpcResult<ReferenceMutationResponse>;
}
