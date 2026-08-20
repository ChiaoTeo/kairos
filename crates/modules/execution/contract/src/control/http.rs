use kairos_protocol::control::{
    ControlAction, HttpControlCodec, HttpControlRequest, HttpControlResponse,
};

use super::{
    ExecutionRestRequest, ExecutionRestResponse, ExecutionRoutesQuery, ReconcileExecutionRequest,
    SubmitIntentRequest,
};

#[derive(Clone, Copy, Debug, Default)]
pub struct ExecutionHttpControl;

impl HttpControlCodec for ExecutionHttpControl {
    type Request = ExecutionRestRequest;
    type Response = ExecutionRestResponse;

    fn component(&self) -> &'static str {
        "execution"
    }

    fn decode(
        &self,
        request: HttpControlRequest<'_>,
    ) -> Result<ControlAction<Self::Request>, HttpControlResponse> {
        let (path, query) = request
            .target
            .split_once('?')
            .unwrap_or((request.target, ""));
        let typed = match (request.method, path) {
            ("POST", "/v1/stop") => return Ok(ControlAction::Stop),
            ("GET", "/v1/health") => ExecutionRestRequest::Health,
            ("GET", "/v1/routes") => ExecutionRestRequest::Routes(routes_query(query)?),
            ("POST", "/v1/intents") => {
                ExecutionRestRequest::SubmitIntent(decode::<SubmitIntentRequest>(request.body)?)
            },
            ("POST", "/v1/reconciliation") => {
                ExecutionRestRequest::Reconcile(decode::<ReconcileExecutionRequest>(request.body)?)
            },
            ("DELETE", _) if path.starts_with("/v1/orders/") => ExecutionRestRequest::CancelOrder {
                order_id: kairos_primitives::execution::OrderId::new(
                    path.trim_start_matches("/v1/orders/").to_owned(),
                )
                .map_err(domain_error)?,
                request: decode_or_default(request.body)?,
            },
            ("PATCH", _) if path.starts_with("/v1/orders/") => ExecutionRestRequest::ReplaceOrder {
                order_id: kairos_primitives::execution::OrderId::new(
                    path.trim_start_matches("/v1/orders/").to_owned(),
                )
                .map_err(domain_error)?,
                request: decode(request.body)?,
            },
            _ => return Err(error(404, "unknown Execution endpoint")),
        };
        Ok(ControlAction::Request(typed))
    }

    fn encode(&self, response: Self::Response) -> HttpControlResponse {
        match response {
            ExecutionRestResponse::Health(Ok(value)) => json(200, &value),
            ExecutionRestResponse::Routes(Ok(value)) => json(200, &value),
            ExecutionRestResponse::SubmitIntent(Ok(value)) => json(201, &value),
            ExecutionRestResponse::CancelOrder(Ok(value))
            | ExecutionRestResponse::ReplaceOrder(Ok(value)) => json(202, &value),
            ExecutionRestResponse::Reconcile(Ok(value)) => json(202, &value),
            ExecutionRestResponse::Health(Err(value))
            | ExecutionRestResponse::Routes(Err(value))
            | ExecutionRestResponse::SubmitIntent(Err(value))
            | ExecutionRestResponse::CancelOrder(Err(value))
            | ExecutionRestResponse::ReplaceOrder(Err(value))
            | ExecutionRestResponse::Reconcile(Err(value)) => {
                json(422, &serde_json::json!({"error": value}))
            },
        }
    }

    fn readiness_request(&self) -> Self::Request {
        ExecutionRestRequest::Health
    }
}

fn routes_query(query: &str) -> Result<ExecutionRoutesQuery, HttpControlResponse> {
    let value = |key: &str| {
        query.split('&').find_map(|part| {
            part.split_once('=')
                .filter(|(name, _)| *name == key)
                .map(|(_, value)| value.to_owned())
        })
    };
    Ok(ExecutionRoutesQuery {
        account_id: value("account_id")
            .map(kairos_primitives::account::AccountId::new)
            .transpose()
            .map_err(domain_error)?,
        segment_key: value("segment_key")
            .map(kairos_primitives::account::SegmentKey::new)
            .transpose()
            .map_err(domain_error)?,
        instrument_id: value("instrument_id")
            .map(kairos_primitives::reference::InstrumentId::new)
            .transpose()
            .map_err(domain_error)?,
        market_id: value("market_id")
            .map(kairos_primitives::reference::MarketId::new)
            .transpose()
            .map_err(domain_error)?,
        participant_id: value("participant_id")
            .map(kairos_primitives::integration::ParticipantId::new)
            .transpose()
            .map_err(domain_error)?,
    })
}

fn decode<T: serde::de::DeserializeOwned>(body: &[u8]) -> Result<T, HttpControlResponse> {
    serde_json::from_slice(body).map_err(|decode_error| error(400, decode_error.to_string()))
}

fn decode_or_default<T: serde::de::DeserializeOwned + Default>(
    body: &[u8],
) -> Result<T, HttpControlResponse> {
    if body.is_empty() {
        Ok(T::default())
    } else {
        decode(body)
    }
}

fn domain_error(error_value: kairos_primitives::DomainTypeError) -> HttpControlResponse {
    error(400, error_value.to_string())
}

fn error(status: u16, message: impl Into<String>) -> HttpControlResponse {
    json(status, &serde_json::json!({"error": message.into()}))
}

fn json(status: u16, value: &impl serde::Serialize) -> HttpControlResponse {
    match serde_json::to_vec(value) {
        Ok(body) => HttpControlResponse::json(status, body),
        Err(_) => HttpControlResponse::json(
            500,
            br#"{"error":"encode Execution control response"}"#.to_vec(),
        ),
    }
}

#[cfg(test)]
mod tests {
    use kairos_protocol::control::{ControlAction, HttpControlCodec, HttpControlRequest};

    use super::ExecutionHttpControl;
    use crate::ExecutionRestRequest;

    #[test]
    fn dynamic_path_and_query_decode_to_typed_operations() {
        let codec = ExecutionHttpControl;
        let routes = codec
            .decode(HttpControlRequest {
                method: "GET",
                target: "/v1/routes?account_id=main&participant_id=binance",
                body: &[],
            })
            .unwrap();
        assert!(matches!(
            routes,
            ControlAction::Request(ExecutionRestRequest::Routes(_))
        ));

        let cancel = codec
            .decode(HttpControlRequest {
                method: "DELETE",
                target: "/v1/orders/order-1",
                body: &[],
            })
            .unwrap();
        assert!(matches!(
            cancel,
            ControlAction::Request(ExecutionRestRequest::CancelOrder { .. })
        ));
    }
}
