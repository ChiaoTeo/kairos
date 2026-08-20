use kairos_protocol::control::{
    ControlAction, HttpControlCodec, HttpControlRequest, HttpControlResponse,
};

use super::{MarketDataSourcesQuery, MarketRestRequest, MarketRestResponse};

pub struct MarketHttpControl;

impl HttpControlCodec for MarketHttpControl {
    type Request = MarketRestRequest;
    type Response = MarketRestResponse;

    fn component(&self) -> &'static str {
        "market"
    }

    fn decode(
        &self,
        request: HttpControlRequest<'_>,
    ) -> Result<ControlAction<Self::Request>, HttpControlResponse> {
        let (path, query) = request
            .target
            .split_once('?')
            .unwrap_or((request.target, ""));
        let business = match (request.method, path) {
            ("POST", "/v1/stop") => return Ok(ControlAction::Stop),
            ("GET", "/v1/health") => MarketRestRequest::Health,
            ("GET", "/v1/data-sources") => {
                MarketRestRequest::DataSources(data_sources_query(query)?)
            },
            ("POST", "/v1/subscribe") | ("POST", "/v1/subscriptions") => {
                MarketRestRequest::Subscribe(decode(request.body)?)
            },
            ("POST", "/v1/unsubscribe") | ("DELETE", _)
                if path.starts_with("/v1/subscriptions/") =>
            {
                MarketRestRequest::Unsubscribe(decode(request.body)?)
            },
            ("POST", "/v1/subscriptions/release-owner") => {
                MarketRestRequest::ReleaseOwner(decode(request.body)?)
            },
            ("POST", "/v1/recover") | ("POST", "/v1/recovery") => MarketRestRequest::Recover,
            ("POST", "/v1/replay/pause") => MarketRestRequest::PauseReplay,
            ("POST", "/v1/replay/resume") => MarketRestRequest::ResumeReplay,
            _ => return Err(error(404, "unknown Market endpoint")),
        };
        Ok(ControlAction::Request(business))
    }

    fn encode(&self, response: Self::Response) -> HttpControlResponse {
        match response {
            MarketRestResponse::Health(Ok(value)) => json(200, &value),
            MarketRestResponse::DataSources(Ok(value)) => json(200, &value),
            MarketRestResponse::Subscribe(Ok(value)) => json(201, &value),
            MarketRestResponse::Unsubscribe(Ok(value))
            | MarketRestResponse::Recover(Ok(value))
            | MarketRestResponse::PauseReplay(Ok(value))
            | MarketRestResponse::ResumeReplay(Ok(value)) => json(202, &value),
            MarketRestResponse::ReleaseOwner(Ok(value)) => json(200, &value),
            MarketRestResponse::Health(Err(value))
            | MarketRestResponse::DataSources(Err(value))
            | MarketRestResponse::Subscribe(Err(value))
            | MarketRestResponse::Unsubscribe(Err(value))
            | MarketRestResponse::ReleaseOwner(Err(value))
            | MarketRestResponse::Recover(Err(value))
            | MarketRestResponse::PauseReplay(Err(value))
            | MarketRestResponse::ResumeReplay(Err(value)) => {
                json(422, &serde_json::json!({"error": value}))
            },
        }
    }

    fn readiness_request(&self) -> Self::Request {
        MarketRestRequest::Health
    }
}

fn data_sources_query(query: &str) -> Result<MarketDataSourcesQuery, HttpControlResponse> {
    let value = |key: &str| {
        query.split('&').find_map(|part| {
            part.split_once('=')
                .filter(|(name, _)| *name == key)
                .map(|(_, value)| value.to_owned())
        })
    };
    let invalid = |cause: kairos_primitives::DomainTypeError| error(400, &cause.to_string());
    Ok(MarketDataSourcesQuery {
        market_id: value("market_id")
            .map(kairos_primitives::reference::MarketId::new)
            .transpose()
            .map_err(invalid)?,
        instrument_id: value("instrument_id")
            .map(kairos_primitives::reference::InstrumentId::new)
            .transpose()
            .map_err(invalid)?,
        exchange: value("exchange")
            .map(kairos_primitives::reference::Exchange::new)
            .transpose()
            .map_err(invalid)?,
        market_type: value("market_type")
            .map(|value| value.parse())
            .transpose()
            .map_err(invalid)?,
        asset_type: value("asset_type")
            .map(|value| value.parse())
            .transpose()
            .map_err(invalid)?,
    })
}

fn decode<T: serde::de::DeserializeOwned>(body: &[u8]) -> Result<T, HttpControlResponse> {
    serde_json::from_slice(body).map_err(|cause| error(400, &cause.to_string()))
}

fn json<T: serde::Serialize>(status: u16, value: &T) -> HttpControlResponse {
    HttpControlResponse::json(
        status,
        serde_json::to_vec(value)
            .unwrap_or_else(|_| br#"{"error":"Market response encoding failed"}"#.to_vec()),
    )
}

fn error(status: u16, message: &str) -> HttpControlResponse {
    json(status, &serde_json::json!({"error": message}))
}
