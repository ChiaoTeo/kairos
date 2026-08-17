use kairos_account_contract::AccountEventFrame;
use kairos_execution_contract::ExecutionEventFrame;
use kairos_integration::application::{
    ExternalAccountEventEnvelope, ExternalEventEnvelope, ExternalExecutionEvent, MarketEvent,
};
use kairos_market_contract::MarketEventFrame;
use kairos_reference_contract::ReferenceEventFrame;
use kairos_risk_contract::RiskEventFrame;

use crate::{Contract, RestRequestOf};

/// Normalized provider facts delivered through the same Actor event loop.
pub enum IntegrationEvent {
    Account(ExternalAccountEventEnvelope),
    Execution(ExternalEventEnvelope<ExternalExecutionEvent>),
    Market(ExternalEventEnvelope<MarketEvent>),
}

/// Every event source visible to a Conflux Actor.
pub enum ConfluxEvent<C: Contract, Local> {
    Rest(RestRequestOf<C>),
    Account(AccountEventFrame),
    Execution(ExecutionEventFrame),
    Market(MarketEventFrame),
    Reference(ReferenceEventFrame),
    Risk(RiskEventFrame),
    Integration(IntegrationEvent),
    Local(Local),
}
