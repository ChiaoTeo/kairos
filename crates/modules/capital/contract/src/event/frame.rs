use std::sync::Arc;

use kairos_protocol::generated::kairos::capital::v_2 as fb;
use kairos_protocol::{BorrowedEventView, BusinessEventKind};

use crate::ContractResult;

pub enum DecodedCapitalEvent<'a> {
    FundingObjectiveChanged(fb::FundingObjectiveChanged<'a>),
    CapitalDemandChanged(fb::CapitalDemandChanged<'a>),
    PolicyChanged(fb::CapitalPolicyChanged<'a>),
    FactsObserved(fb::CapitalFactsObserved<'a>),
    AvailabilityEvaluated(fb::CapitalAvailabilityEvaluated<'a>),
    RouteChanged(fb::CapitalRouteChanged<'a>),
    PlanAuthorized(fb::CapitalPlanAuthorized<'a>),
    PlanStateChanged(fb::CapitalPlanStateChanged<'a>),
    PlanExpired(fb::CapitalPlanExpired<'a>),
}

pub type CapitalEventView<'a> = DecodedCapitalEvent<'a>;

impl<'a> DecodedCapitalEvent<'a> {
    pub fn kind(&self) -> CapitalEventKind {
        <Self as BorrowedEventView<'a>>::kind(self)
    }

    pub fn metadata(&self) -> kairos_protocol::generated::kairos::common::v_2::EventMetadata<'a> {
        <Self as BorrowedEventView<'a>>::metadata(self)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CapitalEventKind {
    FundingObjectiveChanged,
    CapitalDemandChanged,
    PolicyChanged,
    FactsObserved,
    AvailabilityEvaluated,
    RouteChanged,
    PlanAuthorized,
    PlanStateChanged,
    PlanExpired,
}

impl CapitalEventKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::FundingObjectiveChanged => "funding_objective_changed",
            Self::CapitalDemandChanged => "capital_demand_changed",
            Self::PolicyChanged => "policy_changed",
            Self::FactsObserved => "facts_observed",
            Self::AvailabilityEvaluated => "availability_evaluated",
            Self::RouteChanged => "route_changed",
            Self::PlanAuthorized => "plan_authorized",
            Self::PlanStateChanged => "plan_state_changed",
            Self::PlanExpired => "plan_expired",
        }
    }
}

impl BusinessEventKind for CapitalEventKind {
    fn as_str(self) -> &'static str {
        self.as_str()
    }
}

impl<'a> BorrowedEventView<'a> for DecodedCapitalEvent<'a> {
    type Kind = CapitalEventKind;

    fn kind(&self) -> Self::Kind {
        match self {
            Self::FundingObjectiveChanged(_) => CapitalEventKind::FundingObjectiveChanged,
            Self::CapitalDemandChanged(_) => CapitalEventKind::CapitalDemandChanged,
            Self::PolicyChanged(_) => CapitalEventKind::PolicyChanged,
            Self::FactsObserved(_) => CapitalEventKind::FactsObserved,
            Self::AvailabilityEvaluated(_) => CapitalEventKind::AvailabilityEvaluated,
            Self::RouteChanged(_) => CapitalEventKind::RouteChanged,
            Self::PlanAuthorized(_) => CapitalEventKind::PlanAuthorized,
            Self::PlanStateChanged(_) => CapitalEventKind::PlanStateChanged,
            Self::PlanExpired(_) => CapitalEventKind::PlanExpired,
        }
    }

    fn metadata(&self) -> kairos_protocol::generated::kairos::common::v_2::EventMetadata<'a> {
        match self {
            Self::FundingObjectiveChanged(value) => value.metadata(),
            Self::CapitalDemandChanged(value) => value.metadata(),
            Self::PolicyChanged(value) => value.metadata(),
            Self::FactsObserved(value) => value.metadata(),
            Self::AvailabilityEvaluated(value) => value.metadata(),
            Self::RouteChanged(value) => value.metadata(),
            Self::PlanAuthorized(value) => value.metadata(),
            Self::PlanStateChanged(value) => value.metadata(),
            Self::PlanExpired(value) => value.metadata(),
        }
    }
}

pub struct CapitalEventFrame {
    bytes: Arc<[u8]>,
}

impl CapitalEventFrame {
    pub(crate) fn new(bytes: Vec<u8>) -> Self {
        Self {
            bytes: bytes.into(),
        }
    }

    pub fn bytes(&self) -> &[u8] {
        &self.bytes
    }

    pub fn decode(&self) -> ContractResult<DecodedCapitalEvent<'_>> {
        super::decode::decode_event(&self.bytes)
    }
}
