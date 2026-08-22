//! Reference connected/runtime application facade.
//!
//! This facade is used by connected CLI entry points that talk to a running
//! Reference server through the module contract. It must not be used by
//! standalone catalog queries.

use kairos_primitives::integration::ProviderId;
use kairos_primitives::reference::InstrumentId;
use kairos_reference_contract::{
    ReferenceControlRpcClient, ReferenceRuntimeStatusResponse, ReferenceSourceControlRequest,
    ReferenceSourceDefinitionRequest, UpsertAssetRequest, UpsertInstrumentRequest,
    UpsertListingRequest,
};
use serde_json::{Value, json};

pub struct ConnectedReferenceApplication<C> {
    client: C,
}

impl<C> ConnectedReferenceApplication<C>
where
    C: ReferenceControlRpcClient,
{
    pub fn connect(client: C) -> Self {
        Self { client }
    }

    pub async fn runtime_status(
        &self,
    ) -> Result<ReferenceRuntimeStatusResponse, Box<dyn std::error::Error>> {
        Ok(ReferenceControlRpcClient::status(&self.client).await?)
    }

    pub async fn status(&self) -> Result<Value, Box<dyn std::error::Error>> {
        Ok(serde_json::to_value(self.runtime_status().await?)?)
    }

    pub async fn refresh(
        &self,
        source_id: Option<ProviderId>,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        Ok(serde_json::to_value(
            ReferenceControlRpcClient::refresh(&self.client, source_id).await?,
        )?)
    }

    pub async fn publish(&self) -> Result<Value, Box<dyn std::error::Error>> {
        Ok(serde_json::to_value(
            ReferenceControlRpcClient::publish(&self.client).await?,
        )?)
    }

    pub async fn add_option_coverage(
        &self,
        underlying: InstrumentId,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        Ok(serde_json::to_value(
            ReferenceControlRpcClient::add_option_coverage(&self.client, underlying).await?,
        )?)
    }

    pub async fn remove_option_coverage(
        &self,
        underlying: InstrumentId,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        Ok(serde_json::to_value(
            ReferenceControlRpcClient::remove_option_coverage(&self.client, underlying).await?,
        )?)
    }

    pub async fn upsert_source_definition(
        &self,
        request: ReferenceSourceDefinitionRequest,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        Ok(serde_json::to_value(
            ReferenceControlRpcClient::upsert_source_definition(&self.client, request).await?,
        )?)
    }

    pub async fn set_source_desired_state(
        &self,
        request: ReferenceSourceControlRequest,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        Ok(serde_json::to_value(
            ReferenceControlRpcClient::set_source_desired_state(&self.client, request).await?,
        )?)
    }

    pub async fn upsert_asset(
        &self,
        request: UpsertAssetRequest,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        Ok(serde_json::to_value(
            ReferenceControlRpcClient::upsert_asset(&self.client, request).await?,
        )?)
    }

    pub async fn upsert_instrument(
        &self,
        request: UpsertInstrumentRequest,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        Ok(serde_json::to_value(
            ReferenceControlRpcClient::upsert_instrument(&self.client, request).await?,
        )?)
    }

    pub async fn upsert_listing(
        &self,
        request: UpsertListingRequest,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        Ok(serde_json::to_value(
            ReferenceControlRpcClient::upsert_listing(&self.client, request).await?,
        )?)
    }

    pub fn summarize_providers(
        status: ReferenceRuntimeStatusResponse,
        source_filter: Option<String>,
        show: bool,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let mut values = Vec::new();
        for source in status.sources {
            let source_id = json_string(&source.source_id)?;
            if let Some(expected) = source_filter.as_deref() {
                if source_id != expected {
                    continue;
                }
            }
            values.push(json!({
                "source_id": source_id,
                "provider_id": source.provider_id,
                "provider_product": source.provider_product,
                "desired_state": source.desired_state,
                "sync_policy": source.sync_policy,
                "phase": json_string(&source.phase)?,
                "progress": json_string(&source.progress.kind)?,
                "pages_done": source.progress.pages_done,
                "pages_total": source.progress.pages_total,
                "records_seen": source.progress.records_seen,
                "records_changed": source.progress.records_changed,
                "work_item_id": source.work_item.work_item_id,
                "scope_id": source.work_item.scope_id,
                "scope_kind": source.work_item.scope_kind,
                "cursor_present": source.work_item.cursor_present,
                "stale": source.stale,
                "last_known_good": source.has_last_known_good,
                "consecutive_failures": source.consecutive_failures,
                "last_success_unix_nanos": source.last_success_unix_nanos,
                "last_attempt_unix_nanos": source.last_attempt_unix_nanos,
                "retry_after_unix_nanos": source.retry_after_unix_nanos,
                "retry_backoff_seconds": source.retry_backoff_seconds,
            }));
        }
        if show {
            match values.as_slice() {
                [value] => Ok(value.clone()),
                [] => Err(format!(
                    "unknown Reference source/provider: {}",
                    source_filter.expect("show has source filter")
                )
                .into()),
                _ => Err("Reference source/provider query is ambiguous".into()),
            }
        } else {
            Ok(json!(values))
        }
    }

    pub fn summarize_doctor(
        status: ReferenceRuntimeStatusResponse,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let diagnostics = status
            .diagnostics
            .into_iter()
            .map(|diagnostic| {
                Ok(json!({
                    "severity": json_string(&diagnostic.severity)?,
                    "code": diagnostic.code,
                    "source_id": diagnostic.source_id,
                    "message": diagnostic.message,
                    "next_action": diagnostic.next_action,
                }))
            })
            .collect::<Result<Vec<_>, Box<dyn std::error::Error>>>()?;
        Ok(json!({
            "status": json_string(&status.status)?,
            "app_phase": json_string(&status.app_runtime.phase)?,
            "catalog_readiness": json_string(&status.catalog.readiness)?,
            "generation": status.catalog.generation,
            "event_sequence": status.catalog.event_sequence,
            "market_count": status.catalog.market_count,
            "source_count": status.sources.len(),
            "degraded_source_count": status.sources.iter().filter(|source| source.stale || source.consecutive_failures > 0).count(),
            "pending_publication_count": status.publication.pending_publication_count,
            "diagnostics": diagnostics,
        }))
    }

    pub fn summarize_coverage(status: ReferenceRuntimeStatusResponse) -> Value {
        json!({
            "kind": "massive_options",
            "option_underlyings": status.coverage.option_underlyings,
        })
    }
}

fn json_string(value: &impl serde::Serialize) -> Result<String, Box<dyn std::error::Error>> {
    serde_json::to_value(value)?
        .as_str()
        .map(str::to_owned)
        .ok_or_else(|| "expected JSON string value".into())
}
