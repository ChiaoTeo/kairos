use kairos_protocol::control::{
    ControlAction, HttpControlCodec, HttpControlRequest, HttpControlResponse,
};
use serde::Serialize;

use super::{AuthorizeRequest, RiskControlError, RiskRestRequest, RiskRestResponse};

/// HTTP wire mapping for the closed Risk control Contract.
///
/// Listener and process lifecycle are owned by Conflux.  This type owns only
/// the stable conversion between HTTP wire facts and typed Risk operations.
#[derive(Clone, Copy, Debug, Default)]
pub struct RiskHttpControl;

impl HttpControlCodec for RiskHttpControl {
    type Request = RiskRestRequest;
    type Response = RiskRestResponse;

    fn component(&self) -> &'static str {
        "risk"
    }

    fn decode(
        &self,
        request: HttpControlRequest<'_>,
    ) -> Result<ControlAction<Self::Request>, HttpControlResponse> {
        let path = request
            .target
            .split_once('?')
            .map_or(request.target, |(path, _)| path);
        if path == kairos_workspace::runtime::STOP_PATH {
            return if request.method == "POST" {
                Ok(ControlAction::Stop)
            } else {
                Err(error(405, "stop accepts only POST"))
            };
        }
        if path == kairos_workspace::runtime::HEALTH_PATH {
            return if request.method == "GET" {
                Ok(ControlAction::Request(RiskRestRequest::Health))
            } else {
                Err(error(405, "health accepts only GET"))
            };
        }
        if request.method != "POST" {
            return Err(error(
                405,
                "Risk business queries use typed mmap views; control accepts commands",
            ));
        }

        let typed = match path {
            "/v1/publish_policy" => RiskRestRequest::PublishPolicy(decode(request.body)?),
            "/v1/authorizations" | "/v1/authorize_and_reserve" => {
                RiskRestRequest::AuthorizeAndReserve(decode::<AuthorizeRequest>(request.body)?)
            },
            "/v1/pre_trade_check" => RiskRestRequest::PreTradeCheck(decode(request.body)?),
            "/v1/post_trade_check" => RiskRestRequest::PostTradeCheck(decode(request.body)?),
            "/v1/open_circuit" => RiskRestRequest::OpenCircuit(decode(request.body)?),
            "/v1/close_circuit" => RiskRestRequest::CloseCircuit(decode(request.body)?),
            "/v1/resize" => RiskRestRequest::ResizeReservation(decode(request.body)?),
            path if path == "/v1/release"
                || (path.starts_with("/v1/reservations/") && path.ends_with("/release")) =>
            {
                RiskRestRequest::ReleaseReservation(decode(request.body)?)
            },
            path if path == "/v1/consume"
                || (path.starts_with("/v1/reservations/") && path.ends_with("/consume")) =>
            {
                RiskRestRequest::ConsumeReservation(decode(request.body)?)
            },
            "/v1/time/advance" => RiskRestRequest::AdvanceTime(decode(request.body)?),
            _ => return Err(error(404, "unknown Risk control path")),
        };
        Ok(ControlAction::Request(typed))
    }

    fn encode(&self, response: Self::Response) -> HttpControlResponse {
        match response {
            RiskRestResponse::Health(result) => encode_result(result),
            RiskRestResponse::PublishPolicy(result) => encode_result(result),
            RiskRestResponse::AuthorizeAndReserve(result) => encode_result(result),
            RiskRestResponse::PreTradeCheck(result) => encode_result(result),
            RiskRestResponse::PostTradeCheck(result) => encode_result(result),
            RiskRestResponse::OpenCircuit(result) => encode_result(result),
            RiskRestResponse::CloseCircuit(result) => encode_result(result),
            RiskRestResponse::ResizeReservation(result) => encode_result(result),
            RiskRestResponse::ReleaseReservation(result) => encode_result(result),
            RiskRestResponse::ConsumeReservation(result) => encode_result(result),
            RiskRestResponse::AdvanceTime(result) => encode_result(result),
        }
    }

    fn readiness_request(&self) -> Self::Request {
        RiskRestRequest::Health
    }
}

fn decode<T: serde::de::DeserializeOwned>(body: &[u8]) -> Result<T, HttpControlResponse> {
    serde_json::from_slice(body).map_err(|decode_error| {
        json(
            422,
            &serde_json::json!({
                "error": "invalid Risk request",
                "details": decode_error.to_string(),
            }),
        )
    })
}

fn encode_result<T: Serialize>(result: Result<T, RiskControlError>) -> HttpControlResponse {
    match result {
        Ok(value) => match serde_json::to_vec(&value) {
            Ok(body) => HttpControlResponse::json(200, body),
            Err(encode_error) => error(500, format!("encode Risk response: {encode_error}")),
        },
        Err(error_value) => json(
            422,
            &serde_json::json!({
                "error": error_value.message,
                "code": error_value.code,
                "retryable": error_value.retryable,
                "details": error_value.details,
            }),
        ),
    }
}

fn error(status: u16, message: impl Into<String>) -> HttpControlResponse {
    json(status, &serde_json::json!({"error": message.into()}))
}

fn json(status: u16, value: &serde_json::Value) -> HttpControlResponse {
    match serde_json::to_vec(value) {
        Ok(body) => HttpControlResponse::json(status, body),
        Err(_) => {
            HttpControlResponse::json(500, br#"{"error":"encode control response"}"#.to_vec())
        },
    }
}

#[cfg(test)]
mod tests {
    use kairos_protocol::control::{ControlAction, HttpControlCodec, HttpControlRequest};

    use super::RiskHttpControl;
    use crate::RiskRestRequest;

    #[test]
    fn health_and_stop_are_closed_typed_actions() {
        let codec = RiskHttpControl;
        let health = codec
            .decode(HttpControlRequest {
                method: "GET",
                target: "/v1/health",
                body: &[],
            })
            .unwrap();
        assert!(matches!(
            health,
            ControlAction::Request(RiskRestRequest::Health)
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
    fn invalid_method_path_and_json_keep_existing_status_semantics() {
        let codec = RiskHttpControl;
        let method = codec
            .decode(HttpControlRequest {
                method: "POST",
                target: "/v1/health",
                body: &[],
            })
            .unwrap_err();
        assert_eq!(method.status, 405);

        let path = codec
            .decode(HttpControlRequest {
                method: "POST",
                target: "/v1/unknown",
                body: &[],
            })
            .unwrap_err();
        assert_eq!(path.status, 404);

        let json = codec
            .decode(HttpControlRequest {
                method: "POST",
                target: "/v1/publish_policy",
                body: b"not-json",
            })
            .unwrap_err();
        assert_eq!(json.status, 422);
    }
}
