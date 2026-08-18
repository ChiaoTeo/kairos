use kairos_account_contract::AccountEventFrame;
use kairos_execution_contract::ExecutionEventFrame;
use kairos_integration::{ConnectionDescriptor, ExternalParticipantEvent};
use kairos_market_contract::MarketEventFrame;
use kairos_reference_contract::ReferenceEventFrame;
use kairos_risk_contract::RiskEventFrame;

use crate::{Contract, ResourceState, RestRequestOf};

pub struct ContractEvent<F> {
    pub client: String,
    pub frame: F,
}

pub enum SystemEvent {
    ConnectionStateChanged {
        connection: ManagedConnectionIdentity,
        state: ResourceState,
        error: Option<String>,
    },
    SourceReady {
        source: String,
    },
    SourceFailed {
        source: String,
        error: String,
    },
    Timer {
        name: String,
        fired_at_unix_nanos: u64,
    },
}

/// Normalized provider facts delivered through the same Actor event loop.
pub struct IntegrationEvent {
    pub identity: ManagedConnectionIdentity,
    pub event: ExternalParticipantEvent,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ManagedConnectionIdentity {
    pub descriptor: ConnectionDescriptor,
    pub generation: u64,
}

/// Every event source visible to a Conflux Actor.
pub enum ConfluxEvent<C: Contract, Local> {
    Rest(RestRequestOf<C>),
    Account(ContractEvent<AccountEventFrame>),
    Execution(ContractEvent<ExecutionEventFrame>),
    Market(ContractEvent<MarketEventFrame>),
    Reference(ContractEvent<ReferenceEventFrame>),
    Risk(ContractEvent<RiskEventFrame>),
    Integration(IntegrationEvent),
    System(SystemEvent),
    Local(Local),
}
