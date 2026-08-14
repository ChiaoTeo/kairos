use super::view::ExecutionEvent;
use crate::{ContractError, ContractResult};
use kairos_protocol::generated::kairos::execution::v_2 as fb;
pub fn decode_event(bytes: &[u8]) -> ContractResult<ExecutionEvent<'_>> {
    macro_rules! root {
        ($check:ident, $decode:ident, $variant:ident) => {
            if fb::$check(bytes) {
                return fb::$decode(bytes)
                    .map(ExecutionEvent::$variant)
                    .map_err(|error| ContractError::Invalid(error.to_string()));
            }
        };
    }
    root!(
        intent_accepted_buffer_has_identifier,
        root_as_intent_accepted,
        IntentAccepted
    );
    root!(
        intent_rejected_buffer_has_identifier,
        root_as_intent_rejected,
        IntentRejected
    );
    root!(
        plan_created_buffer_has_identifier,
        root_as_plan_created,
        PlanCreated
    );
    root!(
        order_submitted_buffer_has_identifier,
        root_as_order_submitted,
        OrderSubmitted
    );
    root!(
        order_accepted_buffer_has_identifier,
        root_as_order_accepted,
        OrderAccepted
    );
    root!(
        order_rejected_buffer_has_identifier,
        root_as_order_rejected,
        OrderRejected
    );
    root!(
        order_canceled_buffer_has_identifier,
        root_as_order_canceled,
        OrderCanceled
    );
    root!(
        order_expired_buffer_has_identifier,
        root_as_order_expired,
        OrderExpired
    );
    root!(
        fill_recorded_buffer_has_identifier,
        root_as_fill_recorded,
        FillRecorded
    );
    root!(
        reconciliation_required_buffer_has_identifier,
        root_as_reconciliation_required,
        ReconciliationRequired
    );
    Err(ContractError::Invalid(
        "unknown Execution v2 event identifier".into(),
    ))
}
