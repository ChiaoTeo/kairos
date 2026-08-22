//! Market connected/runtime application facade.
//!
//! This facade is used by connected CLI entry points that talk to a running
//! Market server through the module contract or read its published views.
//! Standalone CLI commands must use `CliMarketApplication`.

use kairos_market_contract::{
    MarketClient, MarketCommandEnvelope, MarketControlRpcClient, MarketDataSourcesQuery,
    MarketDataSourcesResponse, MarketSubscribePayload, MarketUnsubscribePayload,
    SnapshotEnvelopeMetadata, ViewMetadata,
};
use kairos_primitives::integration::ProviderId;
use kairos_primitives::market::{ObservationKind, SourceId};
use kairos_primitives::reference::{InstrumentId, MarketId};
use serde_json::Value;

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ConnectedMarketSourceQuery {
    pub market_id: Option<MarketId>,
    pub instrument_id: Option<InstrumentId>,
    pub observation_kind: Option<ObservationKind>,
    pub provider_id: Option<ProviderId>,
    pub configured_only: bool,
    pub ready_only: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ConnectedSourceAvailability {
    Available,
    NotReady,
    NotAvailable,
}

impl ConnectedMarketSourceQuery {
    fn into_contract(self) -> MarketDataSourcesQuery {
        MarketDataSourcesQuery {
            market_id: self.market_id,
            instrument_id: self.instrument_id,
            observation_kind: self.observation_kind,
            provider_id: self.provider_id,
            configured_only: self.configured_only,
            ready_only: self.ready_only,
            ..MarketDataSourcesQuery::default()
        }
    }
}

pub struct ConnectedMarketApplication {
    client: MarketClient,
}

impl ConnectedMarketApplication {
    pub fn connect(client: MarketClient) -> Self {
        Self { client }
    }

    pub async fn health(&self) -> Result<Value, Box<dyn std::error::Error>> {
        Ok(serde_json::to_value(self.client.control().health().await?)?)
    }

    pub async fn sources(
        &self,
        query: ConnectedMarketSourceQuery,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let response = self.source_catalog(query.into_contract()).await?;
        Ok(serde_json::to_value(response)?)
    }

    async fn source_catalog(
        &self,
        query: MarketDataSourcesQuery,
    ) -> Result<MarketDataSourcesResponse, Box<dyn std::error::Error>> {
        Ok(self.client.control().data_sources(query).await?)
    }

    pub async fn source_availability(
        &self,
        market_id: MarketId,
        source_id: &SourceId,
        observation_kind: Option<ObservationKind>,
    ) -> Result<ConnectedSourceAvailability, Box<dyn std::error::Error>> {
        let response = self
            .source_catalog(MarketDataSourcesQuery {
                market_id: Some(market_id),
                observation_kind,
                configured_only: true,
                ..MarketDataSourcesQuery::default()
            })
            .await?;
        Ok(
            match response
                .sources
                .iter()
                .find(|source| &source.source_id == source_id)
            {
                Some(source) if source.ready => ConnectedSourceAvailability::Available,
                Some(_) => ConnectedSourceAvailability::NotReady,
                None => ConnectedSourceAvailability::NotAvailable,
            },
        )
    }

    pub async fn subscribe(
        &self,
        command: MarketCommandEnvelope<MarketSubscribePayload>,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let response = self.client.control().subscribe(command).await?;
        Ok(serde_json::to_value(response)?)
    }

    pub async fn unsubscribe(
        &self,
        command: MarketCommandEnvelope<MarketUnsubscribePayload>,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let response = self.client.control().unsubscribe(command).await?;
        Ok(serde_json::to_value(response)?)
    }

    pub async fn recover(&self) -> Result<Value, Box<dyn std::error::Error>> {
        Ok(serde_json::to_value(
            self.client.control().recover().await?,
        )?)
    }

    pub async fn pause_replay(&self) -> Result<Value, Box<dyn std::error::Error>> {
        Ok(serde_json::to_value(
            self.client.control().pause_replay().await?,
        )?)
    }

    pub async fn resume_replay(&self) -> Result<Value, Box<dyn std::error::Error>> {
        Ok(serde_json::to_value(
            self.client.control().resume_replay().await?,
        )?)
    }

    pub fn quote_snapshot(
        &self,
        market_id: String,
        source_id: String,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let result = (|| -> Result<Value, Box<dyn std::error::Error>> {
            let snapshot =
                self.client
                    .quote(market_id.clone(), source_id.clone(), None::<String>)?;
            let frame = snapshot.read()?;
            let envelope = frame.envelope_metadata();
            let view = frame.view()?;
            let latest = view.quote();
            let quote = latest.value();
            Ok(snapshot_json(
                "quote",
                &market_id,
                &source_id,
                None,
                snapshot.key().canonical_key(),
                envelope,
                view_metadata_json(view.metadata()),
                true,
                serde_json::json!({
                    "quote_id": quote.quote_id(),
                    "instrument_id": quote.instrument_id(),
                    "source_id": quote.source_id(),
                    "bid_price": decimal_json(quote.bid_price()),
                    "bid_quantity": decimal_json(quote.bid_quantity()),
                    "ask_price": decimal_json(quote.ask_price()),
                    "ask_quantity": decimal_json(quote.ask_quantity()),
                    "bid_venue_code": quote.bid_venue_code(),
                    "ask_venue_code": quote.ask_venue_code(),
                    "tape": quote.tape(),
                    "source_observed_at_unix_nanos": quote.source_observed_at_unix_nanos(),
                    "received_at_unix_nanos": quote.received_at_unix_nanos(),
                    "source_event_id": latest.source_event_id(),
                }),
            ))
        })();
        Ok(result.unwrap_or_else(|error| {
            snapshot_error_json("quote", &market_id, &source_id, None, error.as_ref())
        }))
    }

    pub fn bar_snapshot(
        &self,
        market_id: String,
        source_id: String,
        timeframe: String,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let result = (|| -> Result<Value, Box<dyn std::error::Error>> {
            let snapshot = self.client.bar_window(
                market_id.clone(),
                source_id.clone(),
                Some(timeframe.clone()),
            )?;
            let frame = snapshot.read()?;
            let envelope = frame.envelope_metadata();
            let view = frame.view()?;
            let bars = view
                .bars()
                .iter()
                .map(|window| {
                    let bar = window.value();
                    serde_json::json!({
                        "instrument_id": bar.instrument_id(),
                        "source_id": bar.source_id(),
                        "bar_spec_id": bar.bar_spec_id(),
                        "bar_kind": bar.kind().variant_name().unwrap_or("UNKNOWN").to_ascii_lowercase(),
                        "window_start_unix_nanos": bar.window_start_unix_nanos(),
                        "window_end_unix_nanos": bar.window_end_unix_nanos(),
                        "open": decimal_json(Some(bar.open())),
                        "high": decimal_json(Some(bar.high())),
                        "low": decimal_json(Some(bar.low())),
                        "close": decimal_json(Some(bar.close())),
                        "volume": decimal_json(bar.volume()),
                        "source_observed_at_unix_nanos": bar.source_observed_at_unix_nanos(),
                        "received_at_unix_nanos": bar.received_at_unix_nanos(),
                        "source_event_id": window.source_event_id(),
                    })
                })
                .collect::<Vec<_>>();
            let present = !bars.is_empty();
            Ok(snapshot_json(
                "bar",
                &market_id,
                &source_id,
                Some(&timeframe),
                snapshot.key().canonical_key(),
                envelope,
                view_metadata_json(view.metadata()),
                present,
                serde_json::json!({
                    "shard_id": view.shard_id(),
                    "shard_count": view.shard_count(),
                    "bars": bars,
                }),
            ))
        })();
        Ok(result.unwrap_or_else(|error| {
            snapshot_error_json(
                "bar",
                &market_id,
                &source_id,
                Some(&timeframe),
                error.as_ref(),
            )
        }))
    }

    pub fn greeks_snapshot(
        &self,
        market_id: String,
        source_id: String,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let result = (|| -> Result<Value, Box<dyn std::error::Error>> {
            let snapshot =
                self.client
                    .greeks(market_id.clone(), source_id.clone(), None::<String>)?;
            let frame = snapshot.read()?;
            let envelope = frame.envelope_metadata();
            let view = frame.view()?;
            let latest = view.greeks();
            let greeks = latest.value();
            Ok(snapshot_json(
                "greeks",
                &market_id,
                &source_id,
                None,
                snapshot.key().canonical_key(),
                envelope,
                view_metadata_json(view.metadata()),
                true,
                serde_json::json!({
                    "instrument_id": greeks.instrument_id(),
                    "source_id": greeks.source_id(),
                    "expiry_unix_nanos": greeks.expiry_unix_nanos(),
                    "strike": decimal_json(greeks.strike()),
                    "delta": decimal_json(greeks.delta()),
                    "gamma": decimal_json(greeks.gamma()),
                    "vega": decimal_json(greeks.vega()),
                    "theta": decimal_json(greeks.theta()),
                    "implied_volatility": decimal_json(greeks.implied_volatility()),
                    "source_observed_at_unix_nanos": greeks.source_observed_at_unix_nanos(),
                    "received_at_unix_nanos": greeks.received_at_unix_nanos(),
                    "derivation_id": greeks.derivation_id(),
                    "source_event_id": latest.source_event_id(),
                }),
            ))
        })();
        Ok(result.unwrap_or_else(|error| {
            snapshot_error_json("greeks", &market_id, &source_id, None, error.as_ref())
        }))
    }

    pub fn freshness_snapshot(
        &self,
        market_id: String,
        source_id: String,
        qualifier: Option<String>,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let result = (|| -> Result<Value, Box<dyn std::error::Error>> {
            let snapshot =
                self.client
                    .freshness(market_id.clone(), source_id.clone(), qualifier.clone())?;
            let frame = snapshot.read()?;
            let envelope = frame.envelope_metadata();
            let view = frame.view()?;
            let entry = view.entry();
            let status = entry
                .status()
                .variant_name()
                .unwrap_or("UNKNOWN")
                .to_ascii_lowercase();
            Ok(snapshot_json(
                "freshness",
                &market_id,
                &source_id,
                qualifier.as_deref(),
                snapshot.key().canonical_key(),
                envelope,
                view_metadata_json(view.metadata()),
                true,
                serde_json::json!({
                    "source_id": entry.source_id(),
                    "data_kind": entry.data_kind(),
                    "last_event_time_unix_nanos": entry.last_event_time_unix_nanos(),
                    "last_received_time_unix_nanos": entry.last_received_time_unix_nanos(),
                    "age_nanos": entry.age_nanos(),
                    "event_sequence": entry.event_sequence(),
                    "freshness_status": status,
                }),
            ))
        })();
        Ok(result.unwrap_or_else(|error| {
            snapshot_error_json(
                "freshness",
                &market_id,
                &source_id,
                qualifier.as_deref(),
                error.as_ref(),
            )
        }))
    }
}

fn snapshot_json(
    kind: &str,
    market_id: &str,
    source_id: &str,
    qualifier: Option<&str>,
    view_key: String,
    envelope: SnapshotEnvelopeMetadata,
    view_metadata: Value,
    present: bool,
    value: Value,
) -> Value {
    serde_json::json!({
        "kind": kind,
        "market_id": market_id,
        "source_id": source_id,
        "qualifier": qualifier,
        "view_key": view_key,
        "status": if present { "ready" } else { "not_found" },
        "present": present,
        "generation": envelope.generation,
        "envelope_metadata": {
            "resource_epoch": envelope.resource_epoch,
            "producer_incarnation": envelope.producer_incarnation,
            "generation": envelope.generation,
            "applied_event_sequence": envelope.applied_event_sequence,
            "published_at_unix_nanos": envelope.published_at_unix_nanos,
        },
        "view_metadata": view_metadata,
        "value": value,
    })
}

fn snapshot_error_json(
    kind: &str,
    market_id: &str,
    source_id: &str,
    qualifier: Option<&str>,
    error: &(dyn std::error::Error + 'static),
) -> Value {
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
    serde_json::json!({
        "kind": kind,
        "market_id": market_id,
        "source_id": source_id,
        "qualifier": qualifier,
        "status": "unavailable",
        "present": false,
        "value": Value::Null,
        "error": {
            "code": code,
            "message": user_message,
            "retryable": retryable,
            "details": {
                "market_id": market_id,
                "source_id": source_id,
                "kind": kind,
                "qualifier": qualifier,
            }
        }
    })
}

fn decimal_json(
    value: Option<&kairos_protocol::generated::kairos::common::v_2::Decimal64>,
) -> Value {
    value.map_or(Value::Null, |value| {
        serde_json::json!({
            "mantissa": value.mantissa(),
            "scale": value.scale(),
        })
    })
}

fn view_metadata_json(metadata: ViewMetadata<'_>) -> Value {
    serde_json::json!({
        "view_key": metadata.view_key(),
        "generation": metadata.generation(),
        "applied_revision": metadata.applied_revision(),
        "completeness": metadata
            .completeness()
            .variant_name()
            .unwrap_or("UNKNOWN")
            .to_ascii_lowercase(),
    })
}

#[cfg(test)]
mod tests {
    use std::io;

    use super::*;

    #[test]
    fn missing_view_error_is_structured_without_exposing_os_message() {
        let error = io::Error::new(io::ErrorKind::NotFound, "No such file or directory");
        let value = snapshot_error_json(
            "quote",
            "market:binance:spot:BTCUSDT",
            "binance-spot",
            None,
            &error,
        );

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
        let value = snapshot_error_json("quote", "market:test", "source", None, &error);

        assert_eq!(value["error"]["code"], "snapshot_corrupt");
        assert_eq!(value["error"]["retryable"], false);
    }
}
