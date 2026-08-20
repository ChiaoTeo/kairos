use std::sync::Arc;

use kairos_protocol::generated::kairos::capital::v_2 as fb;

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
