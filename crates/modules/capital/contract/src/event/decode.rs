use kairos_protocol::generated::kairos::capital::v_2 as fb;

use super::DecodedCapitalEvent;
use crate::{ContractError, ContractResult};

pub fn decode_event(bytes: &[u8]) -> ContractResult<DecodedCapitalEvent<'_>> {
    if !kairos_protocol::flatbuffer::identifier_is_readable(bytes) {
        return Err(ContractError::Invalid(
            "Capital v2 event payload is shorter than the FlatBuffers header".into(),
        ));
    }
    macro_rules! decode {
        ($has:ident, $root:ident, $variant:ident) => {
            if fb::$has(bytes) {
                return fb::$root(bytes)
                    .map(DecodedCapitalEvent::$variant)
                    .map_err(|error| ContractError::Invalid(error.to_string()));
            }
        };
    }
    decode!(
        funding_objective_changed_buffer_has_identifier,
        root_as_funding_objective_changed,
        FundingObjectiveChanged
    );
    decode!(
        capital_demand_changed_buffer_has_identifier,
        root_as_capital_demand_changed,
        CapitalDemandChanged
    );
    decode!(
        capital_policy_changed_buffer_has_identifier,
        root_as_capital_policy_changed,
        PolicyChanged
    );
    decode!(
        capital_facts_observed_buffer_has_identifier,
        root_as_capital_facts_observed,
        FactsObserved
    );
    decode!(
        capital_availability_evaluated_buffer_has_identifier,
        root_as_capital_availability_evaluated,
        AvailabilityEvaluated
    );
    decode!(
        capital_route_changed_buffer_has_identifier,
        root_as_capital_route_changed,
        RouteChanged
    );
    decode!(
        capital_plan_authorized_buffer_has_identifier,
        root_as_capital_plan_authorized,
        PlanAuthorized
    );
    decode!(
        capital_plan_state_changed_buffer_has_identifier,
        root_as_capital_plan_state_changed,
        PlanStateChanged
    );
    decode!(
        capital_plan_expired_buffer_has_identifier,
        root_as_capital_plan_expired,
        PlanExpired
    );
    Err(ContractError::Invalid(
        "unknown Capital v2 event identifier".into(),
    ))
}

#[cfg(test)]
mod short_frame_tests {
    use super::decode_event;

    #[test]
    fn short_event_frame_is_rejected_without_panicking() {
        assert!(decode_event(b"invalid").is_err());
    }
}
