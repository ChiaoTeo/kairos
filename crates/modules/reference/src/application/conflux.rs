use std::convert::Infallible;

use kairos_conflux::{ConfluxActor, ConfluxEvent, Context, Contract, RestContract, SystemEvent};
use kairos_primitives::reference::InstrumentId;
use kairos_reference_contract::{
    ReferenceControlError, ReferenceHealthResponse, ReferenceMutationResponse,
    ReferenceOptionCoverageResponse, ReferenceProviderHealth,
};

use super::ReferenceApplication;
use crate::domain::ReferenceError;

const EVENT_BATCH_LIMIT: usize = 1_024;

pub struct ReferenceRest;

impl RestContract for ReferenceRest {
    type Request = ();
    type Response = ();
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
    ) -> Result<Option<()>, Self::FatalError> {
        let response = match event {
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
