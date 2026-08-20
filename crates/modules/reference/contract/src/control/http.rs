use kairos_primitives::integration::ProviderId;
use kairos_primitives::reference::InstrumentId;
use kairos_protocol::control::{
    ControlAction, HttpControlCodec, HttpControlRequest, HttpControlResponse,
};

use super::{
    ReferenceControlError, ReferenceOptionCoverageRequest, ReferenceRestRequest,
    ReferenceRestResponse, ReferenceSourceControlRequest,
};

pub const REFRESH: &str = "/v1/refresh";
pub const PUBLISH: &str = "/v1/publish";
pub const ASSETS: &str = "/v1/assets";
pub const INSTRUMENTS: &str = "/v1/instruments";
pub const LISTINGS: &str = "/v1/listings";
pub const SOURCE_PAUSE: &str = "/v1/sources/pause";
pub const SOURCE_RESUME: &str = "/v1/sources/resume";
pub const OPTIONS_COVERAGE_ADD: &str = "/v1/options/coverage/add";
pub const OPTIONS_COVERAGE_REMOVE: &str = "/v1/options/coverage/remove";

pub struct ReferenceHttpControl;

impl HttpControlCodec for ReferenceHttpControl {
    type Request = ReferenceRestRequest;
    type Response = ReferenceRestResponse;

    fn component(&self) -> &'static str {
        "reference"
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
                Ok(ControlAction::Request(ReferenceRestRequest::Health))
            } else {
                Err(error(405, "health accepts only GET"))
            };
        }
        if request.method != "POST" {
            return Err(error(
                405,
                "Reference business queries use the contract-owned SQLite client",
            ));
        }
        let business = match path {
            REFRESH => ReferenceRestRequest::Refresh {
                source_id: query_value(request.target, "source")
                    .map(|value| ProviderId::new(value).map_err(|_| error(400, "invalid source")))
                    .transpose()?,
            },
            PUBLISH => ReferenceRestRequest::Publish,
            SOURCE_PAUSE => ReferenceRestRequest::PauseSource(ReferenceSourceControlRequest {
                source_id: ProviderId::new(required_query(request.target, "source")?)
                    .map_err(|_| error(400, "invalid source"))?,
            }),
            SOURCE_RESUME => ReferenceRestRequest::ResumeSource(ReferenceSourceControlRequest {
                source_id: ProviderId::new(required_query(request.target, "source")?)
                    .map_err(|_| error(400, "invalid source"))?,
            }),
            OPTIONS_COVERAGE_ADD => {
                ReferenceRestRequest::AddOptionCoverage(ReferenceOptionCoverageRequest {
                    underlying: InstrumentId::new(required_query(request.target, "underlying")?)
                        .map_err(|_| error(400, "invalid underlying instrument"))?,
                })
            },
            OPTIONS_COVERAGE_REMOVE => {
                ReferenceRestRequest::RemoveOptionCoverage(ReferenceOptionCoverageRequest {
                    underlying: InstrumentId::new(required_query(request.target, "underlying")?)
                        .map_err(|_| error(400, "invalid underlying instrument"))?,
                })
            },
            ASSETS => ReferenceRestRequest::UpsertAsset(decode(request.body)?),
            INSTRUMENTS => ReferenceRestRequest::UpsertInstrument(decode(request.body)?),
            LISTINGS => ReferenceRestRequest::UpsertListing(decode(request.body)?),
            _ => return Err(error(404, "unknown Reference control path")),
        };
        Ok(ControlAction::Request(business))
    }

    fn encode(&self, response: Self::Response) -> HttpControlResponse {
        match response {
            ReferenceRestResponse::Health(result) => result_response(result),
            ReferenceRestResponse::Refresh(result) => result_response(result),
            ReferenceRestResponse::Publish(result) => result_response(result),
            ReferenceRestResponse::PauseSource(result) => result_response(result),
            ReferenceRestResponse::ResumeSource(result) => result_response(result),
            ReferenceRestResponse::AddOptionCoverage(result) => result_response(result),
            ReferenceRestResponse::RemoveOptionCoverage(result) => result_response(result),
            ReferenceRestResponse::UpsertAsset(result) => result_response(result),
            ReferenceRestResponse::UpsertInstrument(result) => result_response(result),
            ReferenceRestResponse::UpsertListing(result) => result_response(result),
        }
    }

    fn readiness_request(&self) -> Self::Request {
        ReferenceRestRequest::Health
    }
}

fn result_response<T: serde::Serialize>(
    result: Result<T, ReferenceControlError>,
) -> HttpControlResponse {
    match result {
        Ok(value) => json(200, &value),
        Err(cause) => json(
            if cause.retryable { 503 } else { 400 },
            &serde_json::json!({"error": cause}),
        ),
    }
}

fn query_value<'a>(target: &'a str, name: &str) -> Option<&'a str> {
    let (_, query) = target.split_once('?')?;
    query.split('&').find_map(|pair| {
        let (key, value) = pair.split_once('=')?;
        (key == name).then_some(value)
    })
}

fn required_query(target: &str, name: &str) -> Result<String, HttpControlResponse> {
    query_value(target, name)
        .map(str::to_owned)
        .ok_or_else(|| error(422, &format!("{name} is required")))
}

fn decode<T: serde::de::DeserializeOwned>(body: &[u8]) -> Result<T, HttpControlResponse> {
    serde_json::from_slice(body).map_err(|cause| {
        json(
            422,
            &serde_json::json!({"error":"invalid Reference request", "details":cause.to_string()}),
        )
    })
}

fn json<T: serde::Serialize>(status: u16, value: &T) -> HttpControlResponse {
    HttpControlResponse::json(
        status,
        serde_json::to_vec(value)
            .unwrap_or_else(|_| br#"{"error":"Reference response encoding failed"}"#.to_vec()),
    )
}

fn error(status: u16, message: &str) -> HttpControlResponse {
    json(status, &serde_json::json!({"error": message}))
}
