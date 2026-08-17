//! Route-scoped resynchronization barrier policy.

use super::readiness::SharedRouteReadiness;

#[cfg(test)]
pub(super) fn resync_required(readiness: &SharedRouteReadiness) -> bool {
    readiness
        .lock()
        .map(|routes| routes.iter().any(|route| route.status == "resync_required"))
        .unwrap_or(true)
}

pub(super) fn resync_targets(readiness: &SharedRouteReadiness) -> Vec<(usize, Option<String>)> {
    readiness
        .lock()
        .map(|routes| {
            routes
                .iter()
                .enumerate()
                .filter(|(_, route)| route.status == "resync_required")
                .map(|(index, route)| (index, route.binding_id.clone()))
                .collect()
        })
        .unwrap_or_default()
}

pub(super) fn release_route_recovery_barrier(readiness: &SharedRouteReadiness, route_index: usize) {
    if let Ok(mut routes) = readiness.lock() {
        if let Some(route) = routes.get_mut(route_index) {
            if route.status == "resync_required" {
                route.status = "recovering";
                route.last_error = None;
            }
        }
    }
}

#[cfg(test)]
pub(super) fn release_recovery_barrier(readiness: &SharedRouteReadiness) {
    if let Ok(mut routes) = readiness.lock() {
        for route in routes
            .iter_mut()
            .filter(|route| route.status == "resync_required")
        {
            route.status = "recovering";
            route.last_error = None;
        }
    }
}
