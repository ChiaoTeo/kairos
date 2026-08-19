use std::convert::Infallible;

use kairos_conflux::{
    ConfluxActor, ConfluxEvent, Context, Contract, ResourceOperationError, RestContract,
    SystemEvent,
};
use kairos_reference_contract::{
    ReferenceControlError, ReferenceHealthResponse, ReferenceMutationResponse,
    ReferenceOptionCoverageResponse, ReferenceProviderHealth, ReferencePublishResponse,
    ReferenceRefreshResponse, ReferenceRestRequest, ReferenceRestResponse,
    ReferenceSourceStatusResponse,
};

use super::ReferenceApplication;
use crate::domain::ReferenceError;

const EVENT_BATCH_LIMIT: usize = 1_024;

pub struct ReferenceRest;

impl RestContract for ReferenceRest {
    type Request = ReferenceRestRequest;
    type Response = ReferenceRestResponse;
}

impl Contract for ReferenceApplication {
    type Rest = ReferenceRest;
}

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
        event: ConfluxEvent<Self, Self::LocalEvent>,
        context: &mut Context<'_, Self>,
    ) -> Result<Option<ReferenceRestResponse>, Self::FatalError> {
        let response = match event {
            ConfluxEvent::Rest(request) => Some(self.handle_rest(request, context).await),
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
                None
            },
            ConfluxEvent::Local(value) => match value {},
            _ => None,
        };
        Ok(response)
    }
}

impl ReferenceApplication {
    async fn handle_rest(
        &mut self,
        request: ReferenceRestRequest,
        context: &mut Context<'_, Self>,
    ) -> ReferenceRestResponse {
        match request {
            ReferenceRestRequest::Health => {
                ReferenceRestResponse::Health(Ok(self.contract_health().await))
            },
            ReferenceRestRequest::Refresh { source_id } => {
                let result = match source_id.as_deref() {
                    Some(source_id) => {
                        self.refresh_source_with_connections(source_id, &mut context.connections())
                            .await
                    },
                    None => {
                        self.refresh_with_connections(&mut context.connections())
                            .await
                    },
                };
                let response = match result {
                    Ok(result) => {
                        let publication_pending = self.publish_pending(context).await.is_err();
                        Ok(ReferenceRefreshResponse {
                            generation: result.generation.get(),
                            event_sequence: result.event_sequence.get(),
                            changed: result.changed,
                            change_count: result.change_count as u64,
                            publication_pending,
                        })
                    },
                    Err(error) => Err(control_error(error)),
                };
                ReferenceRestResponse::Refresh(response)
            },
            ReferenceRestRequest::Publish => {
                let response =
                    self.publish_pending(context)
                        .await
                        .map(|events| ReferencePublishResponse {
                            generation: self.generation().get(),
                            events: events as u64,
                        });
                ReferenceRestResponse::Publish(response)
            },
            ReferenceRestRequest::PauseSource(request) => {
                let source_id = request.source_id;
                let response = self
                    .set_source_paused(&source_id, true)
                    .await
                    .map(|()| ReferenceSourceStatusResponse {
                        source_id,
                        status: "paused".into(),
                    })
                    .map_err(control_error);
                ReferenceRestResponse::PauseSource(response)
            },
            ReferenceRestRequest::ResumeSource(request) => {
                let source_id = request.source_id;
                let response = self
                    .set_source_paused(&source_id, false)
                    .await
                    .map(|()| ReferenceSourceStatusResponse {
                        source_id,
                        status: "resumed".into(),
                    })
                    .map_err(control_error);
                ReferenceRestResponse::ResumeSource(response)
            },
            ReferenceRestRequest::AddOptionCoverage(request) => {
                ReferenceRestResponse::AddOptionCoverage(
                    self.change_option_coverage(request.underlying, true, context)
                        .await,
                )
            },
            ReferenceRestRequest::RemoveOptionCoverage(request) => {
                ReferenceRestResponse::RemoveOptionCoverage(
                    self.change_option_coverage(request.underlying, false, context)
                        .await,
                )
            },
            ReferenceRestRequest::UpsertAsset(request) => {
                let response = match self.upsert_asset(request).await {
                    Ok(generation) => self.mutation_response(generation.get(), context).await,
                    Err(error) => Err(control_error(error)),
                };
                ReferenceRestResponse::UpsertAsset(response)
            },
            ReferenceRestRequest::UpsertInstrument(request) => {
                let response = match self.upsert_instrument(request).await {
                    Ok(generation) => self.mutation_response(generation.get(), context).await,
                    Err(error) => Err(control_error(error)),
                };
                ReferenceRestResponse::UpsertInstrument(response)
            },
            ReferenceRestRequest::UpsertListing(request) => {
                let response = match self.upsert_listing(request).await {
                    Ok(generation) => self.mutation_response(generation.get(), context).await,
                    Err(error) => Err(control_error(error)),
                };
                ReferenceRestResponse::UpsertListing(response)
            },
        }
    }

    async fn contract_health(&mut self) -> ReferenceHealthResponse {
        let model = self.read_model().await;
        let providers = model
            .provider_health()
            .iter()
            .map(|provider| ReferenceProviderHealth {
                source_id: provider.source_id.clone(),
                status: provider.status.clone(),
                stale: provider.stale,
            })
            .collect::<Vec<_>>();
        let degraded = providers.iter().any(|provider| {
            provider.stale || !matches!(provider.status.as_str(), "ready" | "unknown")
        });
        ReferenceHealthResponse {
            status: if degraded { "degraded" } else { "ready" }.into(),
            providers,
        }
    }

    async fn change_option_coverage(
        &mut self,
        underlying: String,
        enabled: bool,
        context: &mut Context<'_, Self>,
    ) -> Result<ReferenceOptionCoverageResponse, ReferenceControlError> {
        #[cfg(not(test))]
        let result = {
            let key = kairos_conflux::ConnectionKey::new(
                crate::services::providers::MassiveOptionsCoverageSource::connection_key(
                    &underlying,
                )
                .map_err(control_error)?,
            )
            .map_err(|error| control_error(ReferenceError::Provider(error)))?;
            let created = if enabled && !self.option_underlyings().contains(&underlying) {
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
            underlyings: self.option_underlyings(),
            generation: result.generation.get(),
            event_sequence: result.event_sequence.get(),
            changed: result.changed,
        })
    }

    async fn mutation_response(
        &mut self,
        generation: u64,
        context: &mut Context<'_, Self>,
    ) -> Result<ReferenceMutationResponse, ReferenceControlError> {
        let events = self.publish_pending(context).await?;
        Ok(ReferenceMutationResponse { generation, events: events as u64 })
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
            let publisher_keys = context
                .system()
                .reference_event_publishers
                .iter()
                .map(|(key, _)| key.clone())
                .collect::<Vec<_>>();
            if publisher_keys.is_empty() {
                return Err(control_error(ReferenceError::Publication(
                    "reference Aeron publisher is not configured".into(),
                )));
            }
            for key in publisher_keys {
                for publication in &publications {
                    context
                        .system()
                        .reference_event_publishers
                        .try_with(&key, |publisher| publisher.publish(publication.payload()))
                        .map_err(|error| {
                            control_error(ReferenceError::Publication(match error {
                                ResourceOperationError::NotFound => {
                                    "reference Aeron publisher disappeared".into()
                                },
                                ResourceOperationError::Operation(error) => error.to_string(),
                            }))
                        })?;
                }
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
