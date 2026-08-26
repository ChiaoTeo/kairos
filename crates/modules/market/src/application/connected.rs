//! Market connected/runtime application facade.
//!
//! This facade is used by connected CLI entry points that talk to a running
//! Market server through the module contract or read its published views.
//! Standalone CLI commands must use `CliMarketApplication`.

use kairos_market_contract::{
    IndexedViewMetadata, MarketClient, MarketCommandEnvelope, MarketCommandStatus,
    MarketControlRpcClient, MarketDataRoutesQuery, MarketDataRoutesResponse, MarketHealthResponse,
    MarketSubscribePayload, MarketSubscriptionResponse, MarketUnsubscribePayload, MarketViewKey,
    MarketViewKind,
};
use kairos_primitives::market::{ObservationKind, Provider};
use kairos_primitives::reference::{InstrumentId, MarketId};
use kairos_primitives::runtime::InstanceIdentity;
use serde::Serialize;

#[derive(Debug, Serialize)]
pub struct MarketSnapshotResult {
    pub kind: &'static str,
    pub market_id: String,
    pub provider: String,
    pub qualifier: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub view_key: Option<String>,
    pub status: &'static str,
    pub present: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub generation: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub envelope_metadata: Option<MarketEnvelopeMetadataResult>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub view_metadata: Option<MarketViewMetadataResult>,
    pub value: Option<MarketSnapshotValue>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<MarketSnapshotError>,
}

#[derive(Debug, Serialize)]
#[serde(untagged)]
pub enum MarketSnapshotValue {
    Quote(MarketQuoteResult),
    Bar(MarketBarResult),
    Greeks(MarketGreeksResult),
    Freshness(MarketFreshnessResult),
}

#[derive(Debug, Serialize)]
pub struct MarketEnvelopeMetadataResult {
    pub resource_epoch: u64,
    pub producer_incarnation: u64,
    pub generation: u64,
    pub applied_event_sequence: u64,
    pub published_at_unix_nanos: u64,
}

#[derive(Debug, Serialize)]
pub struct MarketViewMetadataResult {
    pub view_key: String,
    pub generation: u64,
    pub applied_revision: Option<u64>,
    pub completeness: String,
}

#[derive(Debug, Serialize)]
pub struct MarketDecimalResult {
    pub mantissa: i64,
    pub scale: u8,
}

#[derive(Debug, Serialize)]
pub struct MarketQuoteResult {
    pub quote_id: Option<String>,
    pub instrument_id: String,
    pub provider: String,
    pub bid_price: Option<MarketDecimalResult>,
    pub bid_quantity: Option<MarketDecimalResult>,
    pub ask_price: Option<MarketDecimalResult>,
    pub ask_quantity: Option<MarketDecimalResult>,
    pub bid_venue_code: Option<String>,
    pub ask_venue_code: Option<String>,
    pub tape: u32,
    pub source_observed_at_unix_nanos: u64,
    pub received_at_unix_nanos: u64,
    pub source_event_id: String,
}

#[derive(Debug, Serialize)]
pub struct MarketBarResult {
    pub instrument_id: String,
    pub provider: String,
    pub bar_spec_id: String,
    pub bar_kind: String,
    pub window_start_unix_nanos: u64,
    pub window_end_unix_nanos: u64,
    pub open: Option<MarketDecimalResult>,
    pub high: Option<MarketDecimalResult>,
    pub low: Option<MarketDecimalResult>,
    pub close: Option<MarketDecimalResult>,
    pub volume: Option<MarketDecimalResult>,
    pub source_observed_at_unix_nanos: u64,
    pub received_at_unix_nanos: u64,
    pub source_event_id: String,
}

#[derive(Debug, Serialize)]
pub struct MarketGreeksResult {
    pub instrument_id: String,
    pub provider: String,
    pub expiry_unix_nanos: Option<u64>,
    pub strike: Option<MarketDecimalResult>,
    pub delta: Option<MarketDecimalResult>,
    pub gamma: Option<MarketDecimalResult>,
    pub vega: Option<MarketDecimalResult>,
    pub theta: Option<MarketDecimalResult>,
    pub implied_volatility: Option<MarketDecimalResult>,
    pub source_observed_at_unix_nanos: u64,
    pub received_at_unix_nanos: u64,
    pub derivation_id: Option<String>,
    pub source_event_id: String,
}

#[derive(Debug, Serialize)]
pub struct MarketFreshnessResult {
    pub provider: String,
    pub data_kind: String,
    pub last_event_time_unix_nanos: u64,
    pub last_received_time_unix_nanos: u64,
    pub age_nanos: u64,
    pub event_sequence: u64,
    pub freshness_status: String,
}

#[derive(Debug, Serialize)]
pub struct MarketSnapshotError {
    pub code: &'static str,
    pub message: &'static str,
    pub retryable: bool,
    pub details: MarketSnapshotErrorDetails,
}

#[derive(Debug, Serialize)]
pub struct MarketSnapshotErrorDetails {
    pub market_id: String,
    pub provider: String,
    pub kind: &'static str,
    pub qualifier: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(untagged)]
pub enum ConnectedMarketOutput {
    Health(MarketHealthResponse),
    Routes(MarketDataRoutesResponse),
    Subscription(MarketSubscriptionResponse),
    Command(MarketCommandStatus),
    Snapshot(MarketSnapshotResult),
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ConnectedMarketRouteQuery {
    pub market_id: Option<MarketId>,
    pub instrument_id: Option<InstrumentId>,
    pub observation_kind: Option<ObservationKind>,
    pub provider: Option<Provider>,
    pub configured_only: bool,
    pub ready_only: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ConnectedRouteAvailability {
    Available,
    NotReady,
    NotAvailable,
}

impl ConnectedMarketRouteQuery {
    fn into_contract(self) -> MarketDataRoutesQuery {
        MarketDataRoutesQuery {
            market_id: self.market_id,
            instrument_id: self.instrument_id,
            observation_kind: self.observation_kind,
            provider: self.provider,
            configured_only: self.configured_only,
            ready_only: self.ready_only,
            ..MarketDataRoutesQuery::default()
        }
    }
}

pub struct ConnectedMarketApplication {
    client: MarketClient,
    identity: InstanceIdentity,
}

impl ConnectedMarketApplication {
    pub fn connect(client: MarketClient, identity: InstanceIdentity) -> Self {
        Self { client, identity }
    }

    fn indexed_snapshot(
        &self,
        market_id: &str,
        provider: &str,
        kind: MarketViewKind,
        qualifier: Option<&str>,
    ) -> Result<kairos_market_contract::MarketIndexedSnapshot, Box<dyn std::error::Error>> {
        let key = MarketViewKey::new(market_id, provider, kind, qualifier.map(str::to_owned))?;
        self.client
            .indexed_current(&self.identity)?
            .get(&key)?
            .ok_or_else(|| "the Market runtime has not published this indexed value".into())
    }

    pub async fn health(&self) -> Result<MarketHealthResponse, Box<dyn std::error::Error>> {
        Ok(self.client.control().health().await?)
    }

    pub async fn routes(
        &self,
        query: ConnectedMarketRouteQuery,
    ) -> Result<MarketDataRoutesResponse, Box<dyn std::error::Error>> {
        self.route_catalog(query.into_contract()).await
    }

    async fn route_catalog(
        &self,
        query: MarketDataRoutesQuery,
    ) -> Result<MarketDataRoutesResponse, Box<dyn std::error::Error>> {
        Ok(self.client.control().data_routes(query).await?)
    }

    pub async fn route_availability(
        &self,
        market_id: MarketId,
        provider: &Provider,
        observation_kind: Option<ObservationKind>,
    ) -> Result<ConnectedRouteAvailability, Box<dyn std::error::Error>> {
        let response = self
            .route_catalog(MarketDataRoutesQuery {
                market_id: Some(market_id),
                observation_kind,
                configured_only: true,
                ..MarketDataRoutesQuery::default()
            })
            .await?;
        Ok(
            match response
                .routes
                .iter()
                .find(|route| &route.provider == provider)
            {
                Some(route)
                    if route.state == kairos_market_contract::MarketDataRouteState::Ready =>
                {
                    ConnectedRouteAvailability::Available
                },
                Some(_) => ConnectedRouteAvailability::NotReady,
                None => ConnectedRouteAvailability::NotAvailable,
            },
        )
    }

    pub async fn subscribe(
        &self,
        command: MarketCommandEnvelope<MarketSubscribePayload>,
    ) -> Result<MarketSubscriptionResponse, Box<dyn std::error::Error>> {
        let response = self.client.control().subscribe(command).await?;
        Ok(response)
    }

    pub async fn unsubscribe(
        &self,
        command: MarketCommandEnvelope<MarketUnsubscribePayload>,
    ) -> Result<MarketCommandStatus, Box<dyn std::error::Error>> {
        let response = self.client.control().unsubscribe(command).await?;
        Ok(response)
    }

    pub async fn recover(&self) -> Result<MarketCommandStatus, Box<dyn std::error::Error>> {
        Ok(self.client.control().recover().await?)
    }

    pub async fn pause_replay(&self) -> Result<MarketCommandStatus, Box<dyn std::error::Error>> {
        Ok(self.client.control().pause_replay().await?)
    }

    pub async fn resume_replay(&self) -> Result<MarketCommandStatus, Box<dyn std::error::Error>> {
        Ok(self.client.control().resume_replay().await?)
    }

    pub fn quote_snapshot(
        &self,
        market_id: String,
        provider: String,
    ) -> Result<MarketSnapshotResult, Box<dyn std::error::Error>> {
        let result = (|| -> Result<MarketSnapshotResult, Box<dyn std::error::Error>> {
            let snapshot =
                self.indexed_snapshot(&market_id, &provider, MarketViewKind::Quote, None)?;
            let envelope = snapshot.metadata();
            let view = snapshot.value()?;
            let quote = view
                .quote()
                .ok_or("Market quote indexed value is missing quote")?;
            Ok(snapshot_result(
                "quote",
                &market_id,
                &provider,
                None,
                snapshot.key().canonical_key(),
                envelope,
                indexed_view_metadata_result(snapshot.key(), envelope),
                true,
                MarketSnapshotValue::Quote(MarketQuoteResult {
                    quote_id: quote.quote_id().map(str::to_owned),
                    instrument_id: quote.instrument_id().to_owned(),
                    provider: quote.provider().to_owned(),
                    bid_price: decimal_result(quote.bid_price()),
                    bid_quantity: decimal_result(quote.bid_quantity()),
                    ask_price: decimal_result(quote.ask_price()),
                    ask_quantity: decimal_result(quote.ask_quantity()),
                    bid_venue_code: quote.bid_venue_code().map(str::to_owned),
                    ask_venue_code: quote.ask_venue_code().map(str::to_owned),
                    tape: quote.tape(),
                    source_observed_at_unix_nanos: quote.source_observed_at_unix_nanos(),
                    received_at_unix_nanos: quote.received_at_unix_nanos(),
                    source_event_id: snapshot.source_event_id()?.unwrap_or_default().to_owned(),
                }),
            ))
        })();
        Ok(result.unwrap_or_else(|error| {
            snapshot_error_result("quote", &market_id, &provider, None, error.as_ref())
        }))
    }

    pub fn bar_snapshot(
        &self,
        market_id: String,
        provider: String,
        timeframe: String,
    ) -> Result<MarketSnapshotResult, Box<dyn std::error::Error>> {
        let result = (|| -> Result<MarketSnapshotResult, Box<dyn std::error::Error>> {
            let snapshot = self.indexed_snapshot(
                &market_id,
                &provider,
                MarketViewKind::Bar,
                Some(&timeframe),
            )?;
            let envelope = snapshot.metadata();
            let view = snapshot.value()?;
            let bar = view
                .bar()
                .ok_or("Market bar indexed value is missing bar")?;
            let bar = MarketBarResult {
                instrument_id: bar.instrument_id().to_owned(),
                provider: bar.provider().to_owned(),
                bar_spec_id: bar.bar_spec_id().to_owned(),
                bar_kind: enum_name(bar.kind().variant_name()),
                window_start_unix_nanos: bar.window_start_unix_nanos(),
                window_end_unix_nanos: bar.window_end_unix_nanos(),
                open: decimal_result(Some(bar.open())),
                high: decimal_result(Some(bar.high())),
                low: decimal_result(Some(bar.low())),
                close: decimal_result(Some(bar.close())),
                volume: decimal_result(bar.volume()),
                source_observed_at_unix_nanos: bar.source_observed_at_unix_nanos(),
                received_at_unix_nanos: bar.received_at_unix_nanos(),
                source_event_id: snapshot.source_event_id()?.unwrap_or_default().to_owned(),
            };
            Ok(snapshot_result(
                "bar",
                &market_id,
                &provider,
                Some(&timeframe),
                snapshot.key().canonical_key(),
                envelope,
                indexed_view_metadata_result(snapshot.key(), envelope),
                true,
                MarketSnapshotValue::Bar(bar),
            ))
        })();
        Ok(result.unwrap_or_else(|error| {
            snapshot_error_result(
                "bar",
                &market_id,
                &provider,
                Some(&timeframe),
                error.as_ref(),
            )
        }))
    }

    pub fn greeks_snapshot(
        &self,
        market_id: String,
        provider: String,
    ) -> Result<MarketSnapshotResult, Box<dyn std::error::Error>> {
        let result = (|| -> Result<MarketSnapshotResult, Box<dyn std::error::Error>> {
            let snapshot =
                self.indexed_snapshot(&market_id, &provider, MarketViewKind::Greeks, None)?;
            let envelope = snapshot.metadata();
            let view = snapshot.value()?;
            let greeks = view
                .greeks()
                .ok_or("Market greeks indexed value is missing greeks")?;
            Ok(snapshot_result(
                "greeks",
                &market_id,
                &provider,
                None,
                snapshot.key().canonical_key(),
                envelope,
                indexed_view_metadata_result(snapshot.key(), envelope),
                true,
                MarketSnapshotValue::Greeks(MarketGreeksResult {
                    instrument_id: greeks.instrument_id().to_owned(),
                    provider: greeks.provider().to_owned(),
                    expiry_unix_nanos: greeks.expiry_unix_nanos(),
                    strike: decimal_result(greeks.strike()),
                    delta: decimal_result(greeks.delta()),
                    gamma: decimal_result(greeks.gamma()),
                    vega: decimal_result(greeks.vega()),
                    theta: decimal_result(greeks.theta()),
                    implied_volatility: decimal_result(greeks.implied_volatility()),
                    source_observed_at_unix_nanos: greeks.source_observed_at_unix_nanos(),
                    received_at_unix_nanos: greeks.received_at_unix_nanos(),
                    derivation_id: greeks.derivation_id().map(str::to_owned),
                    source_event_id: snapshot.source_event_id()?.unwrap_or_default().to_owned(),
                }),
            ))
        })();
        Ok(result.unwrap_or_else(|error| {
            snapshot_error_result("greeks", &market_id, &provider, None, error.as_ref())
        }))
    }

    pub fn freshness_snapshot(
        &self,
        market_id: String,
        provider: String,
        qualifier: Option<String>,
    ) -> Result<MarketSnapshotResult, Box<dyn std::error::Error>> {
        let result = (|| -> Result<MarketSnapshotResult, Box<dyn std::error::Error>> {
            let snapshot = self.indexed_snapshot(
                &market_id,
                &provider,
                MarketViewKind::Freshness,
                qualifier.as_deref(),
            )?;
            let envelope = snapshot.metadata();
            let view = snapshot.value()?;
            let entry = view
                .freshness()
                .ok_or("Market freshness indexed value is missing freshness")?;
            let status = entry
                .status()
                .variant_name()
                .unwrap_or("UNKNOWN")
                .to_ascii_lowercase();
            Ok(snapshot_result(
                "freshness",
                &market_id,
                &provider,
                qualifier.as_deref(),
                snapshot.key().canonical_key(),
                envelope,
                indexed_view_metadata_result(snapshot.key(), envelope),
                true,
                MarketSnapshotValue::Freshness(MarketFreshnessResult {
                    provider: entry.provider().to_owned(),
                    data_kind: entry.data_kind().to_owned(),
                    last_event_time_unix_nanos: entry.last_event_time_unix_nanos(),
                    last_received_time_unix_nanos: entry.last_received_time_unix_nanos(),
                    age_nanos: entry.age_nanos(),
                    event_sequence: entry.event_sequence(),
                    freshness_status: status,
                }),
            ))
        })();
        Ok(result.unwrap_or_else(|error| {
            snapshot_error_result(
                "freshness",
                &market_id,
                &provider,
                qualifier.as_deref(),
                error.as_ref(),
            )
        }))
    }
}

fn snapshot_result(
    kind: &'static str,
    market_id: &str,
    provider: &str,
    qualifier: Option<&str>,
    view_key: String,
    envelope: &IndexedViewMetadata,
    view_metadata: MarketViewMetadataResult,
    present: bool,
    value: MarketSnapshotValue,
) -> MarketSnapshotResult {
    MarketSnapshotResult {
        kind,
        market_id: market_id.to_owned(),
        provider: provider.to_owned(),
        qualifier: qualifier.map(str::to_owned),
        view_key: Some(view_key),
        status: if present { "ready" } else { "not_found" },
        present,
        generation: Some(envelope.applied_event_sequence),
        envelope_metadata: Some(MarketEnvelopeMetadataResult {
            resource_epoch: envelope.resource_epoch,
            producer_incarnation: envelope.producer_incarnation,
            generation: envelope.applied_event_sequence,
            applied_event_sequence: envelope.applied_event_sequence,
            published_at_unix_nanos: envelope.committed_at_unix_nanos,
        }),
        view_metadata: Some(view_metadata),
        value: Some(value),
        error: None,
    }
}

fn snapshot_error_result(
    kind: &'static str,
    market_id: &str,
    provider: &str,
    qualifier: Option<&str>,
    error: &(dyn std::error::Error + 'static),
) -> MarketSnapshotResult {
    let message = error.to_string();
    let lowered = message.to_ascii_lowercase();
    let (code, user_message, retryable) = if lowered.contains("no such file")
        || lowered.contains("not found")
        || lowered.contains("snapshot is not initialized")
    {
        (
            "view_not_found",
            "the Market runtime has not published this view",
            true,
        )
    } else if lowered.contains("corrupt")
        || lowered.contains("checksum")
        || lowered.contains("unsupported envelope")
        || lowered.contains("expected ")
    {
        (
            "snapshot_corrupt",
            "the published Market view is invalid",
            false,
        )
    } else {
        (
            "view_unavailable",
            "the Market view could not be read",
            true,
        )
    };
    MarketSnapshotResult {
        kind,
        market_id: market_id.to_owned(),
        provider: provider.to_owned(),
        qualifier: qualifier.map(str::to_owned),
        view_key: None,
        status: "unavailable",
        present: false,
        generation: None,
        envelope_metadata: None,
        view_metadata: None,
        value: None,
        error: Some(MarketSnapshotError {
            code,
            message: user_message,
            retryable,
            details: MarketSnapshotErrorDetails {
                market_id: market_id.to_owned(),
                provider: provider.to_owned(),
                kind,
                qualifier: qualifier.map(str::to_owned),
            },
        }),
    }
}

fn decimal_result(
    value: Option<&kairos_protocol::generated::kairos::common::v_2::Decimal64>,
) -> Option<MarketDecimalResult> {
    value.map(|value| MarketDecimalResult {
        mantissa: value.mantissa(),
        scale: value.scale(),
    })
}

fn indexed_view_metadata_result(
    key: &MarketViewKey,
    metadata: &IndexedViewMetadata,
) -> MarketViewMetadataResult {
    MarketViewMetadataResult {
        view_key: key.canonical_key(),
        generation: metadata.applied_event_sequence,
        applied_revision: Some(metadata.applied_event_sequence),
        completeness: "complete".into(),
    }
}

fn enum_name(value: Option<&str>) -> String {
    value.unwrap_or("UNKNOWN").to_ascii_lowercase()
}

#[cfg(test)]
mod tests {
    use std::io;

    use super::*;

    #[test]
    fn missing_view_error_is_structured_without_exposing_os_message() {
        let error = io::Error::new(io::ErrorKind::NotFound, "No such file or directory");
        let result = snapshot_error_result(
            "quote",
            "market:binance:spot:BTCUSDT",
            "binance-spot",
            None,
            &error,
        );
        let value = serde_json::to_value(result).unwrap();

        assert_eq!(value["status"], "unavailable");
        assert_eq!(value["error"]["code"], "view_not_found");
        assert_eq!(
            value["error"]["message"],
            "the Market runtime has not published this view"
        );
        assert!(
            !value["error"]["message"]
                .as_str()
                .unwrap()
                .contains("No such file")
        );
        assert!(!value.to_string().contains("No such file"));
    }

    #[test]
    fn invalid_view_payload_is_reported_as_corrupt() {
        let error = io::Error::new(io::ErrorKind::InvalidData, "expected QuoteLatestView");
        let value = serde_json::to_value(snapshot_error_result(
            "quote",
            "market:test",
            "source",
            None,
            &error,
        ))
        .unwrap();

        assert_eq!(value["error"]["code"], "snapshot_corrupt");
        assert_eq!(value["error"]["retryable"], false);
    }
}
