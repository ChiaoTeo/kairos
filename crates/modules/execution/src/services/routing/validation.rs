//! Validation for configured Execution route collections.

use super::route::ExecutionRoute;

pub(super) fn validate_routes<C>(routes: &[ExecutionRoute<C>]) -> Result<(), String> {
    if routes.is_empty() {
        return Err("at least one execution route is required".into());
    }
    for (index, route) in routes.iter().enumerate() {
        for other in &routes[index + 1..] {
            if route.route_id == other.route_id {
                return Err(format!("duplicate execution route_id: {}", route.route_id));
            }
            if route.descriptor.binding_id == other.descriptor.binding_id {
                return Err(format!(
                    "duplicate Integration binding_id in Execution routes: {}",
                    route.descriptor.binding_id
                ));
            }
            if route.account_id == other.account_id
                && route.segment_key == other.segment_key
                && route.descriptor.participant == other.descriptor.participant
                && route.participant_instrument_type == other.participant_instrument_type
            {
                return Err(format!(
                    "ambiguous Execution route for account={}, segment={}, participant={}",
                    route.account_id, route.segment_key, route.descriptor.participant.id
                ));
            }
        }
    }
    Ok(())
}
