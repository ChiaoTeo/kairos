//! Market connected/runtime application facade.
//!
//! This facade is used by connected CLI entry points that talk to a running
//! Market server through the module contract or read its published views.
//! Standalone CLI commands must use `CliMarketApplication`.

use kairos_market_contract::{
    MarketClient, MarketCommandEnvelope, MarketControlRpcClient, MarketDataSourcesQuery,
    MarketSubscribePayload, MarketUnsubscribePayload, SnapshotEnvelopeMetadata, ViewMetadata,
};
use serde_json::Value;

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

    pub async fn sources(&self) -> Result<Value, Box<dyn std::error::Error>> {
        let response = self
            .client
            .control()
            .data_sources(MarketDataSourcesQuery::default())
            .await?;
        Ok(serde_json::to_value(response)?)
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
        let snapshot = self.client.quote(market_id, source_id, None::<String>)?;
        let frame = snapshot.read()?;
        let envelope = frame.envelope_metadata();
        let view = frame.view()?;
        Ok(snapshot_json(
            "quote",
            snapshot.key().canonical_key(),
            envelope,
            view_metadata_json(view.metadata()),
            true,
        ))
    }

    pub fn bar_snapshot(
        &self,
        market_id: String,
        source_id: String,
        timeframe: String,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let snapshot = self
            .client
            .bar_window(market_id, source_id, Some(timeframe))?;
        let frame = snapshot.read()?;
        let envelope = frame.envelope_metadata();
        let view = frame.view()?;
        Ok(snapshot_json(
            "bar",
            snapshot.key().canonical_key(),
            envelope,
            view_metadata_json(view.metadata()),
            !view.bars().is_empty(),
        ))
    }

    pub fn greeks_snapshot(
        &self,
        market_id: String,
        source_id: String,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let snapshot = self.client.greeks(market_id, source_id, None::<String>)?;
        let frame = snapshot.read()?;
        let envelope = frame.envelope_metadata();
        let view = frame.view()?;
        Ok(snapshot_json(
            "greeks",
            snapshot.key().canonical_key(),
            envelope,
            view_metadata_json(view.metadata()),
            true,
        ))
    }

    pub fn freshness_snapshot(
        &self,
        market_id: String,
        source_id: String,
        qualifier: Option<String>,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let snapshot = self.client.freshness(market_id, source_id, qualifier)?;
        let frame = snapshot.read()?;
        let envelope = frame.envelope_metadata();
        let view = frame.view()?;
        let entry = view.entry();
        let status = entry
            .status()
            .variant_name()
            .unwrap_or("UNKNOWN")
            .to_ascii_lowercase();
        Ok(snapshot_json_with_payload(
            "freshness",
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
    }
}

fn snapshot_json(
    kind: &str,
    view_key: String,
    envelope: SnapshotEnvelopeMetadata,
    view_metadata: Value,
    present: bool,
) -> Value {
    serde_json::json!({
        "kind": kind,
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
    })
}

fn snapshot_json_with_payload(
    kind: &str,
    view_key: String,
    envelope: SnapshotEnvelopeMetadata,
    view_metadata: Value,
    present: bool,
    payload: Value,
) -> Value {
    serde_json::json!({
        "kind": kind,
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
        "payload": payload,
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
