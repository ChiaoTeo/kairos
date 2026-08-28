mod decode;
mod encode;
mod frame;
mod stream;
mod worker;

use kairos_primitives::time::{Sequence, UnixNanos};

use crate::{
    CapitalAvailability, CapitalDemand, CapitalFacts, CapitalOperation, CapitalPlan, CapitalPolicy,
    CapitalReservation, CapitalRoute, FundingObjective,
};

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CapitalEvent {
    FundingObjectiveChanged {
        objective: FundingObjective,
        event_sequence: Sequence,
        occurred_at: UnixNanos,
    },
    CapitalDemandChanged {
        demand: CapitalDemand,
        event_sequence: Sequence,
        occurred_at: UnixNanos,
    },
    PolicyChanged {
        policy: CapitalPolicy,
        event_sequence: Sequence,
        occurred_at: UnixNanos,
    },
    FactsObserved {
        facts: CapitalFacts,
        event_sequence: Sequence,
        occurred_at: UnixNanos,
    },
    AvailabilityEvaluated {
        availability: Vec<CapitalAvailability>,
        event_sequence: Sequence,
        occurred_at: UnixNanos,
    },
    RouteChanged {
        route: CapitalRoute,
        event_sequence: Sequence,
        occurred_at: UnixNanos,
    },
    PlanAuthorized {
        plan: CapitalPlan,
        reservation: CapitalReservation,
        event_sequence: Sequence,
        occurred_at: UnixNanos,
    },
    PlanStateChanged {
        plan: CapitalPlan,
        reservation: CapitalReservation,
        operation: CapitalOperation,
        event_sequence: Sequence,
        occurred_at: UnixNanos,
    },
    PlanExpired {
        plan: CapitalPlan,
        reservation: CapitalReservation,
        operation: Option<CapitalOperation>,
        event_sequence: Sequence,
        occurred_at: UnixNanos,
    },
}

impl CapitalEvent {
    pub fn sequence(&self) -> Sequence {
        match self {
            Self::FundingObjectiveChanged { event_sequence, .. }
            | Self::CapitalDemandChanged { event_sequence, .. }
            | Self::PolicyChanged { event_sequence, .. }
            | Self::FactsObserved { event_sequence, .. }
            | Self::AvailabilityEvaluated { event_sequence, .. }
            | Self::RouteChanged { event_sequence, .. }
            | Self::PlanAuthorized { event_sequence, .. }
            | Self::PlanStateChanged { event_sequence, .. }
            | Self::PlanExpired { event_sequence, .. } => *event_sequence,
        }
    }

    pub fn occurred_at(&self) -> UnixNanos {
        match self {
            Self::FundingObjectiveChanged { occurred_at, .. }
            | Self::CapitalDemandChanged { occurred_at, .. }
            | Self::PolicyChanged { occurred_at, .. }
            | Self::FactsObserved { occurred_at, .. }
            | Self::AvailabilityEvaluated { occurred_at, .. }
            | Self::RouteChanged { occurred_at, .. }
            | Self::PlanAuthorized { occurred_at, .. }
            | Self::PlanStateChanged { occurred_at, .. }
            | Self::PlanExpired { occurred_at, .. } => *occurred_at,
        }
    }
}

pub use decode::{decode_event, decode_event as decode_event_view};
pub use encode::{CapitalAeronEventPublisher, FlatbuffersCapitalEventWriter};
pub use frame::{CapitalEventFrame, CapitalEventKind, CapitalEventView, DecodedCapitalEvent};
pub use stream::CapitalEventStream;
pub use worker::QueuedCapitalEventPublisher;
