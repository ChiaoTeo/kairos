use kairos_protocol::generated::kairos::risk::v_2 as fb;

use super::view::DecodedRiskEvent;
use crate::{ContractError, ContractResult};
pub fn decode_event(bytes: &[u8]) -> ContractResult<DecodedRiskEvent<'_>> {
    if fb::risk_decision_made_buffer_has_identifier(bytes) {
        return fb::root_as_risk_decision_made(bytes)
            .map(DecodedRiskEvent::DecisionMade)
            .map_err(|e| ContractError::Invalid(e.to_string()));
    }
    if fb::reservation_reserved_buffer_has_identifier(bytes) {
        return fb::root_as_reservation_reserved(bytes)
            .map(DecodedRiskEvent::ReservationReserved)
            .map_err(|e| ContractError::Invalid(e.to_string()));
    }
    if fb::reservation_consumed_buffer_has_identifier(bytes) {
        return fb::root_as_reservation_consumed(bytes)
            .map(DecodedRiskEvent::ReservationConsumed)
            .map_err(|e| ContractError::Invalid(e.to_string()));
    }
    if fb::reservation_released_buffer_has_identifier(bytes) {
        return fb::root_as_reservation_released(bytes)
            .map(DecodedRiskEvent::ReservationReleased)
            .map_err(|e| ContractError::Invalid(e.to_string()));
    }
    if fb::reservation_expired_buffer_has_identifier(bytes) {
        return fb::root_as_reservation_expired(bytes)
            .map(DecodedRiskEvent::ReservationExpired)
            .map_err(|e| ContractError::Invalid(e.to_string()));
    }
    if fb::circuit_opened_buffer_has_identifier(bytes) {
        return fb::root_as_circuit_opened(bytes)
            .map(DecodedRiskEvent::CircuitOpened)
            .map_err(|e| ContractError::Invalid(e.to_string()));
    }
    if fb::circuit_closed_buffer_has_identifier(bytes) {
        return fb::root_as_circuit_closed(bytes)
            .map(DecodedRiskEvent::CircuitClosed)
            .map_err(|e| ContractError::Invalid(e.to_string()));
    }
    Err(ContractError::Invalid(
        "unknown Risk v2 event identifier".into(),
    ))
}
