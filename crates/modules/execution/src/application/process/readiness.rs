//! Execution-owned readiness and recovery-barrier state for configured routes.

pub(super) type SharedRouteReadiness =
    std::sync::Arc<std::sync::Mutex<Vec<ExecutionRouteReadiness>>>;

#[derive(Clone, Debug, serde::Serialize)]
pub(super) struct ExecutionRouteReadiness {
    pub(super) route_id: String,
    pub(super) required: bool,
    pub(super) binding_id: Option<String>,
    pub(super) status: &'static str,
    pub(super) last_error: Option<String>,
}

pub(super) fn set_route_readiness(
    readiness: &SharedRouteReadiness,
    index: usize,
    status: &'static str,
    last_error: Option<String>,
) {
    if let Ok(mut routes) = readiness.lock() {
        if let Some(route) = routes.get_mut(index) {
            route.status = status;
            route.last_error = last_error;
        }
    }
}

pub(super) fn process_readiness(
    readiness: &SharedRouteReadiness,
) -> (&'static str, Vec<ExecutionRouteReadiness>) {
    let routes = readiness
        .lock()
        .map(|routes| routes.clone())
        .unwrap_or_default();
    let required_unready = routes
        .iter()
        .any(|route| route.required && route.status != "ready");
    let optional_unready = routes
        .iter()
        .any(|route| !route.required && route.status != "ready");
    let status = if required_unready {
        "not_ready"
    } else if optional_unready {
        "degraded"
    } else {
        "ready"
    };
    (status, routes)
}

pub(super) fn route_status(readiness: &SharedRouteReadiness, index: usize) -> Option<&'static str> {
    readiness
        .lock()
        .ok()
        .and_then(|routes| routes.get(index).map(|route| route.status))
}
