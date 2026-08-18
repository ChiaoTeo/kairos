//! Massive Indices provider-native catalog and historical bars.

use secrecy::ExposeSecret;

use crate::services::participants::massive::indices::{IndexBarRow, IndicesRestService};
use crate::{
    ConnectionDescriptor, HistoricalBarQuery, HistoricalBarRequest, IntegrationError, MarketBar,
    ParticipantKind, ParticipantRef,
};

use super::{rest::map_exchange_error, MassiveIndexDefinition, MassiveIndicesRestConfig};

pub struct MassiveIndicesRestConnection {
    descriptor: ConnectionDescriptor,
    service: IndicesRestService,
}

impl MassiveIndicesRestConnection {
    pub fn new(
        connection_key: crate::ConnectionKey,
        config: MassiveIndicesRestConfig,
    ) -> Result<Self, IntegrationError> {
        let mut descriptor = ConnectionDescriptor::new(
            connection_key,
            ParticipantRef::new(ParticipantKind::DataProvider, "massive")
                .map_err(IntegrationError::InvalidRequest)?,
            "indices.rest",
        )
        .map_err(IntegrationError::InvalidRequest)?;
        descriptor.environment = config.environment;
        descriptor
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        Ok(Self {
            descriptor,
            service: IndicesRestService::new(
                config.api_key.expose_secret(),
                config.endpoint,
                config.as_of,
            )
            .map_err(map_exchange_error)?,
        })
    }

    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }

    pub async fn fetch_index_definitions(
        &mut self,
    ) -> Result<Vec<MassiveIndexDefinition>, IntegrationError> {
        self.service
            .definitions()
            .await
            .map_err(map_exchange_error)
            .map(|rows| {
                rows.into_iter()
                    .map(|row| MassiveIndexDefinition {
                        ticker: row.ticker,
                        name: row.name,
                        currency: row.currency,
                        source_venue: row.source_venue,
                        active: row.active,
                    })
                    .collect()
            })
    }
}

impl HistoricalBarQuery for MassiveIndicesRestConnection {
    async fn fetch_bars(
        &mut self,
        request: &HistoricalBarRequest,
    ) -> Result<Vec<MarketBar>, IntegrationError> {
        request
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        let (multiplier, timespan) =
            crate::services::participants::massive::market::data::parse_interval(
                &request.interval,
            )?;
        self.service
            .bars(
                request.window.symbol.as_str(),
                multiplier,
                timespan,
                request.window.start_time_unix_nanos.get() / 1_000_000,
                request.window.end_time_unix_nanos.get() / 1_000_000,
            )
            .await
            .map_err(map_exchange_error)?
            .into_iter()
            .map(|row| normalize_bar(row, request))
            .collect()
    }
}

fn normalize_bar(
    row: IndexBarRow,
    request: &HistoricalBarRequest,
) -> Result<MarketBar, IntegrationError> {
    let parse = |value: &str| {
        value
            .parse()
            .map_err(|error: kairos_primitives::DomainTypeError| {
                IntegrationError::InvalidPayload(error.to_string())
            })
    };
    Ok(MarketBar {
        symbol: request.window.symbol.clone(),
        interval: request.interval.clone(),
        open: parse(&row.open)?,
        high: parse(&row.high)?,
        low: parse(&row.low)?,
        close: parse(&row.close)?,
        volume: None,
        opened_at_unix_nanos: row.opened_at_unix_millis.saturating_mul(1_000_000).into(),
        closed_at_unix_nanos: None,
        adjusted: None,
        derivation: "massive-index-value-aggregate".into(),
    })
}
