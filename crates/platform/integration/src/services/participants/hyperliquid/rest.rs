use crate::participants::hyperliquid::HyperliquidRestConfig;
use crate::transport::http::HttpClient;
use crate::{
    ConnectionDescriptor, ConnectionKey, IntegrationError, ParticipantKind, ParticipantRef,
};

pub(crate) struct RestService {
    descriptor: ConnectionDescriptor,
    endpoint: String,
    client: HttpClient,
}

impl RestService {
    pub(crate) fn new(
        connection_key: ConnectionKey,
        config: HyperliquidRestConfig,
        domain: &str,
        principal_id: Option<String>,
    ) -> Result<Self, IntegrationError> {
        let endpoint = config.endpoint.trim_end_matches('/').to_owned();
        if !(endpoint.starts_with("http://") || endpoint.starts_with("https://")) {
            return Err(IntegrationError::InvalidRequest(
                "Hyperliquid REST endpoint must start with http:// or https://".into(),
            ));
        }
        let mut descriptor = ConnectionDescriptor::new(
            connection_key,
            ParticipantRef::new(ParticipantKind::Exchange, "hyperliquid")
                .map_err(IntegrationError::InvalidRequest)?,
            domain,
        )
        .map_err(IntegrationError::InvalidRequest)?;
        descriptor.environment = config.environment;
        descriptor.principal_id = principal_id;
        descriptor
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        Ok(Self {
            descriptor,
            endpoint,
            client: HttpClient::new("kairos-integration/hyperliquid")
                .map_err(|error| IntegrationError::Transport(error.to_string()))?,
        })
    }

    pub(crate) fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }

    pub(crate) fn endpoint(&self) -> &str {
        &self.endpoint
    }

    pub(crate) fn client(&mut self) -> &mut HttpClient {
        &mut self.client
    }
}
