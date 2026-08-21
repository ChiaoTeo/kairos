use kairos_protocol::control::jsonrpc::{RpcResult, conflux_rpc};

#[conflux_rpc(namespace = "account")]
pub trait AccountControlRpc {
    async fn health(&self) -> RpcResult<kairos_account_contract::Health>;

    async fn apply_simulated_settlement(
        &self,
        settlement: kairos_account_contract::SimulatedSettlement,
    ) -> RpcResult<kairos_account_contract::AccountCommandStatus>;

    async fn apply_simulated_capital_mutation(
        &self,
        mutation: kairos_account_contract::SimulatedCapitalMutation,
    ) -> RpcResult<kairos_account_contract::AccountCommandStatus>;

    async fn query_simulated_capital_mutation(
        &self,
        query: kairos_account_contract::SimulatedCapitalMutationQuery,
    ) -> RpcResult<kairos_account_contract::SimulatedCapitalMutationStatusResponse>;

    async fn mark_to_market(
        &self,
        request: kairos_account_contract::MarkToMarketRequest,
    ) -> RpcResult<kairos_account_contract::AccountCommandStatus>;

    async fn advance_time(
        &self,
        request: kairos_account_contract::AdvanceAccountTimeRequest,
    ) -> RpcResult<kairos_account_contract::AdvanceAccountTimeResponse>;

    async fn refresh(
        &self,
        request: kairos_account_contract::AccountSegmentsRequest,
    ) -> RpcResult<kairos_account_contract::AccountRefreshResponse>;

    async fn reconcile(
        &self,
        request: kairos_account_contract::AccountSegmentsRequest,
    ) -> RpcResult<kairos_account_contract::AccountRefreshResponse>;
}
