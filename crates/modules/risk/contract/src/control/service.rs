use kairos_protocol::control::jsonrpc::{RpcResult, conflux_rpc};

#[conflux_rpc(namespace = "risk")]
pub trait RiskControlRpc {
    async fn health(&self) -> RpcResult<kairos_risk_contract::Health>;

    async fn publish_policy(
        &self,
        request: kairos_risk_contract::PublishPolicyRequest,
    ) -> RpcResult<kairos_risk_contract::RiskCommandStatus>;

    async fn authorize_and_reserve(
        &self,
        request: kairos_risk_contract::AuthorizeRequest,
    ) -> RpcResult<kairos_risk_contract::RiskDecision>;

    async fn pre_trade_check(
        &self,
        request: kairos_risk_contract::AuthorizeRequest,
    ) -> RpcResult<kairos_risk_contract::RiskDecision>;

    async fn post_trade_check(
        &self,
        request: kairos_risk_contract::AuthorizeRequest,
    ) -> RpcResult<kairos_risk_contract::RiskDecision>;

    async fn open_circuit(
        &self,
        request: kairos_risk_contract::OpenCircuitRequest,
    ) -> RpcResult<kairos_risk_contract::CircuitState>;

    async fn close_circuit(
        &self,
        request: kairos_risk_contract::CloseCircuitRequest,
    ) -> RpcResult<kairos_risk_contract::CircuitState>;

    async fn resize_reservation(
        &self,
        request: kairos_risk_contract::ResizeReservationRequest,
    ) -> RpcResult<kairos_risk_contract::Reservation>;

    async fn release_reservation(
        &self,
        request: kairos_risk_contract::ReleaseReservationRequest,
    ) -> RpcResult<kairos_risk_contract::Reservation>;

    async fn consume_reservation(
        &self,
        request: kairos_risk_contract::ConsumeReservationRequest,
    ) -> RpcResult<kairos_risk_contract::Reservation>;

    async fn advance_time(
        &self,
        request: kairos_risk_contract::AdvanceRiskTimeRequest,
    ) -> RpcResult<kairos_risk_contract::AdvanceRiskTimeResponse>;
}
