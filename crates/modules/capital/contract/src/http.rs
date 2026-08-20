use kairos_protocol::control::{
    ControlAction, HttpControlCodec, HttpControlRequest, HttpControlResponse,
};

use crate::{CapitalControlError, CapitalRestRequest, CapitalRestResponse, FundingObjectiveStatus};

/// HTTP wire mapping for the closed Capital control Contract.
///
/// Conflux owns listeners, lifecycle, admission queueing, and stop handling.
/// This codec owns only the stable wire-to-business-operation conversion.
#[derive(Clone, Copy, Debug, Default)]
pub struct CapitalHttpControl;

impl HttpControlCodec for CapitalHttpControl {
    type Request = CapitalRestRequest;
    type Response = CapitalRestResponse;

    fn component(&self) -> &'static str {
        "capital"
    }

    fn decode(
        &self,
        request: HttpControlRequest<'_>,
    ) -> Result<ControlAction<Self::Request>, HttpControlResponse> {
        let path = request
            .target
            .split_once('?')
            .map_or(request.target, |(path, _)| path);
        let typed = match (request.method, path) {
            ("POST", "/v1/stop") => return Ok(ControlAction::Stop),
            ("GET", "/v1/health") => CapitalRestRequest::Health,
            ("POST", "/v1/objectives/publish") => {
                CapitalRestRequest::PublishFundingObjective(decode(request.body)?)
            },
            ("POST", "/v1/objectives/cancel") => {
                CapitalRestRequest::CancelFundingObjective(decode(request.body)?)
            },
            ("POST", "/v1/demands/observe") => {
                CapitalRestRequest::ObserveCapitalDemand(decode(request.body)?)
            },
            ("POST", "/v1/availability/query") => {
                CapitalRestRequest::QueryCapitalAvailability(decode(request.body)?)
            },
            ("POST", "/v1/plans/reconcile") => {
                CapitalRestRequest::ReconcileCapitalPlan(decode(request.body)?)
            },
            (_, path) if known_path(path) => {
                return Err(error(
                    405,
                    "method is not allowed for this Capital endpoint",
                ));
            },
            _ => return Err(error(404, "unknown Capital endpoint")),
        };
        Ok(ControlAction::Request(typed))
    }

    fn encode(&self, response: Self::Response) -> HttpControlResponse {
        match response {
            CapitalRestResponse::Health(result) => encode_result(result, 200),
            CapitalRestResponse::PublishFundingObjective(value)
            | CapitalRestResponse::CancelFundingObjective(value) => {
                let status = value
                    .error
                    .as_ref()
                    .map(admission_or_success_status)
                    .unwrap_or(200);
                debug_assert_eq!(
                    value.status == FundingObjectiveStatus::Rejected,
                    value.error.is_some()
                );
                json(status, &value)
            },
            CapitalRestResponse::ObserveCapitalDemand(value) => {
                let status = value
                    .error
                    .as_ref()
                    .map(admission_or_success_status)
                    .unwrap_or(200);
                json(status, &value)
            },
            CapitalRestResponse::QueryCapitalAvailability(result) => match result {
                Ok(value) => json(200, &value),
                Err(value) => {
                    let status = match value.code.as_str() {
                        "capital_internal" | "capital_publication_failed" => 500,
                        "capital_admission_closed" => 503,
                        _ => 404,
                    };
                    json(status, &value)
                },
            },
            CapitalRestResponse::ReconcileCapitalPlan(value) => {
                let status = value
                    .error
                    .as_ref()
                    .map(|error| match error.code.as_str() {
                        "capital_plan_not_found" => 404,
                        "capital_publication_failed" | "capital_internal" => 500,
                        "capital_admission_closed" => 503,
                        _ => 409,
                    })
                    .unwrap_or(200);
                json(status, &value)
            },
        }
    }

    fn readiness_request(&self) -> Self::Request {
        CapitalRestRequest::Health
    }
}

fn known_path(path: &str) -> bool {
    matches!(
        path,
        "/v1/stop"
            | "/v1/health"
            | "/v1/objectives/publish"
            | "/v1/objectives/cancel"
            | "/v1/demands/observe"
            | "/v1/availability/query"
            | "/v1/plans/reconcile"
    )
}

fn decode<T: serde::de::DeserializeOwned>(body: &[u8]) -> Result<T, HttpControlResponse> {
    serde_json::from_slice(body).map_err(|cause| {
        json(
            422,
            &serde_json::json!({
                "error": "invalid Capital request",
                "details": cause.to_string(),
            }),
        )
    })
}

fn encode_result<T: serde::Serialize>(
    result: Result<T, CapitalControlError>,
    success_status: u16,
) -> HttpControlResponse {
    match result {
        Ok(value) => json(success_status, &value),
        Err(value) => {
            let status = match value.code.as_str() {
                "capital_internal" | "capital_publication_failed" => 500,
                _ => 503,
            };
            json(status, &value)
        },
    }
}

fn admission_or_success_status(value: &CapitalControlError) -> u16 {
    match value.code.as_str() {
        "capital_admission_closed" => 503,
        _ => 200,
    }
}

fn error(status: u16, message: impl Into<String>) -> HttpControlResponse {
    json(status, &serde_json::json!({"error": message.into()}))
}

fn json(status: u16, value: &impl serde::Serialize) -> HttpControlResponse {
    match serde_json::to_vec(value) {
        Ok(body) => HttpControlResponse::json(status, body),
        Err(_) => HttpControlResponse::json(
            500,
            br#"{"error":"encode Capital control response"}"#.to_vec(),
        ),
    }
}

#[cfg(test)]
mod tests {
    use kairos_protocol::control::{ControlAction, HttpControlCodec, HttpControlRequest};

    use super::CapitalHttpControl;
    use crate::CapitalRestRequest;

    #[test]
    fn health_and_stop_are_closed_typed_actions() {
        let codec = CapitalHttpControl;
        let health = codec
            .decode(HttpControlRequest {
                method: "GET",
                target: "/v1/health",
                body: &[],
            })
            .unwrap();
        assert!(matches!(
            health,
            ControlAction::Request(CapitalRestRequest::Health)
        ));

        let stop = codec
            .decode(HttpControlRequest {
                method: "POST",
                target: "/v1/stop",
                body: &[],
            })
            .unwrap();
        assert!(matches!(stop, ControlAction::Stop));
    }

    #[test]
    fn business_paths_decode_to_owned_operations() {
        let codec = CapitalHttpControl;
        let invalid = codec
            .decode(HttpControlRequest {
                method: "POST",
                target: "/v1/objectives/publish",
                body: b"not-json",
            })
            .unwrap_err();
        assert_eq!(invalid.status, 422);

        let unknown = codec
            .decode(HttpControlRequest {
                method: "POST",
                target: "/v1/unknown",
                body: &[],
            })
            .unwrap_err();
        assert_eq!(unknown.status, 404);

        let method = codec
            .decode(HttpControlRequest {
                method: "POST",
                target: "/v1/health",
                body: &[],
            })
            .unwrap_err();
        assert_eq!(method.status, 405);
    }
}
