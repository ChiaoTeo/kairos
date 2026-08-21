use kairos_protocol::control::jsonrpc::{RpcResult, conflux_rpc};

#[conflux_rpc(namespace = "reference")]
pub trait ReferenceControlRpc {
    async fn health(&self) -> RpcResult<kairos_reference_contract::ReferenceHealthResponse>;

    async fn refresh(
        &self,
        source_id: Option<kairos_primitives::integration::ProviderId>,
    ) -> RpcResult<kairos_reference_contract::ReferenceRefreshResponse>;

    async fn publish(&self) -> RpcResult<kairos_reference_contract::ReferencePublishResponse>;

    async fn pause_source(
        &self,
        source_id: kairos_primitives::integration::ProviderId,
    ) -> RpcResult<kairos_reference_contract::ReferenceSourceStatusResponse>;

    async fn resume_source(
        &self,
        source_id: kairos_primitives::integration::ProviderId,
    ) -> RpcResult<kairos_reference_contract::ReferenceSourceStatusResponse>;

    async fn add_option_coverage(
        &self,
        underlying: kairos_primitives::reference::InstrumentId,
    ) -> RpcResult<kairos_reference_contract::ReferenceOptionCoverageResponse>;

    async fn remove_option_coverage(
        &self,
        underlying: kairos_primitives::reference::InstrumentId,
    ) -> RpcResult<kairos_reference_contract::ReferenceOptionCoverageResponse>;

    async fn upsert_asset(
        &self,
        request: kairos_reference_contract::UpsertAssetRequest,
    ) -> RpcResult<kairos_reference_contract::ReferenceMutationResponse>;

    async fn upsert_instrument(
        &self,
        request: kairos_reference_contract::UpsertInstrumentRequest,
    ) -> RpcResult<kairos_reference_contract::ReferenceMutationResponse>;

    async fn upsert_listing(
        &self,
        request: kairos_reference_contract::UpsertListingRequest,
    ) -> RpcResult<kairos_reference_contract::ReferenceMutationResponse>;
}
