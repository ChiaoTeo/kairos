use kairos_protocol::control::jsonrpc::{RpcResult, conflux_rpc};

#[conflux_rpc(namespace = "market")]
pub trait MarketControlRpc {
    async fn health(&self) -> RpcResult<kairos_market_contract::MarketHealthResponse>;

    async fn data_routes(
        &self,
        query: kairos_market_contract::MarketDataRoutesQuery,
    ) -> RpcResult<kairos_market_contract::MarketDataRoutesResponse>;

    async fn subscribe(
        &self,
        command: kairos_market_contract::MarketCommandEnvelope<
            kairos_market_contract::MarketSubscribePayload,
        >,
    ) -> RpcResult<kairos_market_contract::MarketSubscriptionResponse>;

    async fn unsubscribe(
        &self,
        command: kairos_market_contract::MarketCommandEnvelope<
            kairos_market_contract::MarketUnsubscribePayload,
        >,
    ) -> RpcResult<kairos_market_contract::MarketCommandStatus>;

    async fn release_owner(
        &self,
        command: kairos_market_contract::MarketCommandEnvelope<
            kairos_market_contract::MarketReleaseOwnerPayload,
        >,
    ) -> RpcResult<kairos_market_contract::MarketReleaseOwnerResponse>;

    async fn recover(&self) -> RpcResult<kairos_market_contract::MarketCommandStatus>;

    async fn pause_replay(&self) -> RpcResult<kairos_market_contract::MarketCommandStatus>;

    async fn resume_replay(&self) -> RpcResult<kairos_market_contract::MarketCommandStatus>;
}
