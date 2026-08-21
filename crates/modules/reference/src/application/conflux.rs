use std::convert::Infallible;

use kairos_conflux::{ConfluxActor, ConfluxEvent, Context, SystemEvent};
use kairos_primitives::integration::ProviderId;
use kairos_primitives::reference::InstrumentId;
use kairos_protocol::control::jsonrpc::{ErrorObjectOwned, RpcResult, business_error};
use kairos_reference_contract::{
    ReferenceControlError, ReferenceHealthResponse, ReferenceMutationResponse,
    ReferenceOptionCoverageResponse, ReferenceProviderHealth, ReferencePublishResponse,
    ReferenceRefreshResponse, ReferenceSourceStatusResponse, UpsertAssetRequest,
    UpsertInstrumentRequest, UpsertListingRequest,
};

use super::{ReferenceApplication, ReferenceRpcActor};
use crate::domain::ReferenceError;

const EVENT_BATCH_LIMIT: usize = 1_024;
const REFERENCE_BUSINESS_ERROR_CODE: i32 = -31_001;

impl ConfluxActor for ReferenceApplication {
    type FatalError = ReferenceError;
    type LocalEvent = Infallible;

    async fn started(&mut self, context: &mut Context<'_, Self>) -> Result<(), Self::FatalError> {
        self.activate_sources(&mut context.connections()).await?;
        if self.initial_refresh() {
            if let Err(error) = self
                .refresh_with_connections(&mut context.connections())
                .await
            {
                tracing::warn!(
                    event = "reference_initial_refresh_deferred",
                    component = "reference",
                    error = %error,
                    "Reference starts from its durable catalog while provider synchronization retries"
                );
            }
        }
        let _ = self.publish_pending(context).await;
        context.spawn_timer("refresh", self.refresh_interval());
        Ok(())
    }

    async fn handle(
        &mut self,
        event: ConfluxEvent,
        context: &mut Context<'_, Self>,
    ) -> Result<(), Self::FatalError> {
        match event {
            ConfluxEvent::System(SystemEvent::Timer { name, .. }) if name == "refresh" => {
                if let Err(error) = self
                    .refresh_with_connections(&mut context.connections())
                    .await
                {
                    tracing::warn!(
                        event = "reference_refresh_failed",
                        component = "reference",
                        error = %error,
                        "Reference retains its last durable catalog"
                    );
                }
                let _ = self.publish_pending(context).await;
            },
            _ => {},
        };
        Ok(())
    }
}

impl ReferenceRpcActor for ReferenceApplication {
    async fn health(
        &mut self,
        (): (),
        _context: &mut Context<'_, Self>,
    ) -> RpcResult<ReferenceHealthResponse> {
        Ok(self.contract_health().await)
    }

    async fn refresh(
        &mut self,
        source_id: Option<ProviderId>,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<ReferenceRefreshResponse> {
        let result = match source_id.as_ref() {
            Some(source_id) => {
                self.refresh_source_with_connections(source_id.as_str(), &mut context.connections())
                    .await
            },
            None => {
                self.refresh_with_connections(&mut context.connections())
                    .await
            },
        }
        .map_err(rpc_reference_error)?;
        self.publish_pending(context)
            .await
            .map_err(rpc_control_error)?;
        Ok(ReferenceRefreshResponse {
            generation: result.generation,
            event_sequence: result.event_sequence,
            changed: result.changed,
            change_count: result.change_count as u64,
            publication_pending: false,
        })
    }

    async fn publish(
        &mut self,
        (): (),
        context: &mut Context<'_, Self>,
    ) -> RpcResult<ReferencePublishResponse> {
        let events = self
            .publish_pending(context)
            .await
            .map_err(rpc_control_error)?;
        Ok(ReferencePublishResponse {
            generation: self.generation(),
            events: events as u64,
        })
    }

    async fn pause_source(
        &mut self,
        source_id: ProviderId,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<ReferenceSourceStatusResponse> {
        self.set_source_paused(source_id.as_str(), true)
            .await
            .map_err(rpc_reference_error)?;
        let _ = self
            .publish_pending(context)
            .await
            .map_err(rpc_control_error)?;
        Ok(ReferenceSourceStatusResponse {
            source_id,
            status: kairos_reference_contract::ReferenceProviderStatus::Paused,
        })
    }

    async fn resume_source(
        &mut self,
        source_id: ProviderId,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<ReferenceSourceStatusResponse> {
        self.set_source_paused(source_id.as_str(), false)
            .await
            .map_err(rpc_reference_error)?;
        let _ = self
            .publish_pending(context)
            .await
            .map_err(rpc_control_error)?;
        Ok(ReferenceSourceStatusResponse {
            source_id,
            status: kairos_reference_contract::ReferenceProviderStatus::Ready,
        })
    }

    async fn add_option_coverage(
        &mut self,
        underlying: InstrumentId,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<ReferenceOptionCoverageResponse> {
        self.change_option_coverage(underlying, true, context)
            .await
            .map_err(rpc_control_error)
    }

    async fn remove_option_coverage(
        &mut self,
        underlying: InstrumentId,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<ReferenceOptionCoverageResponse> {
        self.change_option_coverage(underlying, false, context)
            .await
            .map_err(rpc_control_error)
    }

    async fn upsert_asset(
        &mut self,
        request: UpsertAssetRequest,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<ReferenceMutationResponse> {
        let generation = ReferenceApplication::upsert_asset(self, request)
            .await
            .map_err(rpc_reference_error)?;
        self.mutation_response(generation, context)
            .await
            .map_err(rpc_control_error)
    }

    async fn upsert_instrument(
        &mut self,
        request: UpsertInstrumentRequest,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<ReferenceMutationResponse> {
        let generation = ReferenceApplication::upsert_instrument(self, request)
            .await
            .map_err(rpc_reference_error)?;
        self.mutation_response(generation, context)
            .await
            .map_err(rpc_control_error)
    }

    async fn upsert_listing(
        &mut self,
        request: UpsertListingRequest,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<ReferenceMutationResponse> {
        let generation = ReferenceApplication::upsert_listing(self, request)
            .await
            .map_err(rpc_reference_error)?;
        self.mutation_response(generation, context)
            .await
            .map_err(rpc_control_error)
    }
}

impl ReferenceApplication {
    pub(crate) async fn contract_health(&mut self) -> ReferenceHealthResponse {
        let model = self.read_model().await;
        let providers = model
            .provider_health()
            .iter()
            .map(|provider| ReferenceProviderHealth {
                source_id: kairos_primitives::integration::ProviderId::new(
                    provider.source_id.clone(),
                )
                .expect("normalized provider source identity"),
                status: match provider.status.as_str() {
                    "ready" | "unknown" => {
                        kairos_reference_contract::ReferenceProviderStatus::Ready
                    },
                    "paused" => kairos_reference_contract::ReferenceProviderStatus::Paused,
                    "syncing" => kairos_reference_contract::ReferenceProviderStatus::Syncing,
                    _ => kairos_reference_contract::ReferenceProviderStatus::Degraded,
                },
                stale: provider.stale,
            })
            .collect::<Vec<_>>();
        let degraded = providers.iter().any(|provider| {
            provider.stale
                || !matches!(
                    provider.status,
                    kairos_reference_contract::ReferenceProviderStatus::Ready
                )
        });
        ReferenceHealthResponse {
            status: if degraded {
                kairos_reference_contract::ReferenceHealthStatus::Degraded
            } else {
                kairos_reference_contract::ReferenceHealthStatus::Ready
            },
            providers,
        }
    }

    async fn change_option_coverage(
        &mut self,
        underlying: InstrumentId,
        enabled: bool,
        context: &mut Context<'_, Self>,
    ) -> Result<ReferenceOptionCoverageResponse, ReferenceControlError> {
        #[cfg(not(test))]
        let result = {
            let key = kairos_conflux::ConnectionKey::new(
                crate::services::providers::MassiveOptionsCoverageSource::connection_key(
                    underlying.as_str(),
                )
                .map_err(control_error)?,
            )
            .map_err(|error| control_error(ReferenceError::Provider(error)))?;
            let created = if enabled
                && !self
                    .option_underlyings()
                    .iter()
                    .any(|value| value == underlying.as_str())
            {
                let (planned_key, parameters) = self
                    .massive_option_connection_plan(&underlying)
                    .map_err(control_error)?;
                context
                    .connections()
                    .massive_rest
                    .create(planned_key.clone(), parameters)
                    .map_err(|error| control_error(ReferenceError::Provider(error.to_string())))?;
                Some(planned_key)
            } else {
                None
            };
            let result = self
                .set_managed_option_underlying(
                    &underlying,
                    enabled,
                    created.clone(),
                    &mut context.connections(),
                )
                .await
                .map_err(control_error);
            if result.is_err() {
                if let Some(created) = &created {
                    let _ = context.connections().massive_rest.remove(created);
                }
            } else if !enabled {
                let _ = context.connections().massive_rest.remove(&key);
            }
            result?
        };
        #[cfg(test)]
        let result = self
            .set_option_underlying(&underlying, enabled, &mut context.connections())
            .await
            .map_err(control_error)?;
        let _ = self.publish_pending(context).await;
        Ok(ReferenceOptionCoverageResponse {
            underlying,
            enabled,
            underlyings: self
                .option_underlyings()
                .into_iter()
                .filter_map(|value| InstrumentId::new(value).ok())
                .collect(),
            generation: result.generation,
            event_sequence: result.event_sequence,
            changed: result.changed,
        })
    }

    async fn mutation_response(
        &mut self,
        generation: kairos_primitives::time::Generation,
        context: &mut Context<'_, Self>,
    ) -> Result<ReferenceMutationResponse, ReferenceControlError> {
        let events = self.publish_pending(context).await?;
        Ok(ReferenceMutationResponse {
            generation,
            events: events as u64,
        })
    }

    async fn publish_pending(
        &mut self,
        context: &mut Context<'_, Self>,
    ) -> Result<usize, ReferenceControlError> {
        let publications = self
            .pending_publications(EVENT_BATCH_LIMIT)
            .await
            .map_err(control_error)?;
        if publications.is_empty() {
            return Ok(0);
        }

        let event_ids = {
            const OUTPUT: &str = "reference-changes";
            if !context.outputs().aeron.contains(OUTPUT) {
                return Err(control_error(ReferenceError::Publication(
                    "reference Aeron publisher is not configured".into(),
                )));
            }
            for publication in &publications {
                context
                    .outputs()
                    .aeron
                    .publish(OUTPUT, publication.payload())
                    .map_err(|error| {
                        control_error(ReferenceError::Publication(error.to_string()))
                    })?;
            }
            publications
                .iter()
                .map(|publication| publication.event_id().to_owned())
                .collect::<Vec<_>>()
        };
        self.acknowledge_publications(&event_ids)
            .await
            .map_err(control_error)?;
        Ok(publications.len())
    }
}

fn control_error(error: ReferenceError) -> ReferenceControlError {
    ReferenceControlError {
        code: error.code().into(),
        message: error.to_string(),
        retryable: error.retryable(),
        details: std::collections::BTreeMap::new(),
    }
}

fn rpc_control_error(error: ReferenceControlError) -> ErrorObjectOwned {
    business_error(REFERENCE_BUSINESS_ERROR_CODE, error.message.clone(), error)
}

fn rpc_reference_error(error: ReferenceError) -> ErrorObjectOwned {
    rpc_control_error(control_error(error))
}
