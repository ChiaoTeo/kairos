use kairos_conflux::Context;
use kairos_primitives::integration::{ProviderId, ProviderProductCode};
use kairos_primitives::reference::InstrumentId;
use kairos_reference_contract::{
    ReferenceControlError, ReferenceOptionCoverageResponse, ReferenceProviderProduct,
    ReferenceProviderStatus, ReferenceSourceControlRequest, ReferenceSourceDefinitionRequest,
    ReferenceSourceDesiredState, ReferenceSourceScope, ReferenceSourceScopeKind,
    ReferenceSourceScopeRequest, ReferenceSourceScopeResponse, ReferenceSourceStatusResponse,
    ReferenceSourceSyncPolicy,
};

use crate::application::{ReferenceApplication, ReferenceRefreshResult};
use crate::domain::{
    ReferenceError, ReferenceResult, ReferenceSourceDefinition, SourceCredentialBinding,
    SourceDesiredState, SourceHealth, SourceScope, SourceScopeKind, SourceSyncPolicy,
};

impl ReferenceApplication {
    pub async fn activate_sources(
        &mut self,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<()> {
        self.actor.activate_sources(connections).await
    }

    pub fn source_id(&self) -> &str {
        self.actor.source_id()
    }

    pub fn source_health(&self) -> Vec<SourceHealth> {
        self.actor.source_health()
    }

    pub(crate) async fn set_source_desired_state_from_contract(
        &mut self,
        request: ReferenceSourceControlRequest,
        context: &mut Context<'_, Self>,
    ) -> Result<ReferenceSourceStatusResponse, ReferenceControlError> {
        self.set_source_desired_state_response(
            request.source_id,
            domain_source_desired_state(request.desired_state),
            context,
        )
        .await
    }

    pub(crate) async fn upsert_source_definition_from_contract(
        &mut self,
        request: ReferenceSourceDefinitionRequest,
        context: &mut Context<'_, Self>,
    ) -> Result<ReferenceSourceStatusResponse, ReferenceError> {
        let source_id = request.source_id.clone();
        let desired_state = request.desired_state;
        self.upsert_source_definition_with_connections(
            domain_source_definition(source_id.clone(), request),
            &mut context.connections(),
        )
        .await?;
        Ok(ReferenceSourceStatusResponse {
            source_id,
            status: contract_provider_status(domain_source_desired_state(desired_state)),
        })
    }

    pub(crate) async fn set_source_desired_state_response(
        &mut self,
        source_id: ProviderId,
        desired_state: SourceDesiredState,
        context: &mut Context<'_, Self>,
    ) -> Result<ReferenceSourceStatusResponse, ReferenceControlError> {
        self.set_source_desired_state_with_connections(
            source_id.as_str(),
            desired_state,
            &mut context.connections(),
        )
        .await
        .map_err(control_error)?;
        let _ = self.publish_pending_to_outputs(context).await?;
        Ok(ReferenceSourceStatusResponse {
            source_id,
            status: contract_provider_status(desired_state),
        })
    }

    pub async fn set_source_desired_state(
        &mut self,
        source_id: &str,
        desired_state: SourceDesiredState,
    ) -> ReferenceResult<()> {
        self.actor
            .set_source_desired_state(source_id, desired_state)
            .await
    }

    pub async fn set_source_desired_state_with_connections(
        &mut self,
        source_id: &str,
        desired_state: SourceDesiredState,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<()> {
        self.actor
            .set_source_desired_state_with_connections(source_id, desired_state, connections)
            .await
    }

    pub async fn upsert_source_definition(
        &mut self,
        definition: ReferenceSourceDefinition,
    ) -> ReferenceResult<()> {
        self.actor.upsert_source_definition(definition).await
    }

    pub async fn upsert_source_definition_with_connections(
        &mut self,
        definition: ReferenceSourceDefinition,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<()> {
        self.actor
            .upsert_source_definition_with_connections(definition, connections)
            .await
    }

    pub fn option_underlyings(&self) -> Vec<String> {
        self.actor.option_underlyings()
    }

    pub(crate) async fn change_option_coverage(
        &mut self,
        underlying: InstrumentId,
        enabled: bool,
        context: &mut Context<'_, Self>,
    ) -> Result<ReferenceOptionCoverageResponse, ReferenceControlError> {
        let result = self
            .change_source_scope(
                ReferenceSourceScopeRequest {
                    source_id: ProviderId::new("massive-options")
                        .expect("static provider source id is valid"),
                    scope: ReferenceSourceScope {
                        kind: ReferenceSourceScopeKind::UnderlyingInstrument,
                        id: Some(underlying.as_str().to_owned()),
                    },
                    enabled,
                },
                context,
            )
            .await?;
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

    pub(crate) async fn change_source_scope(
        &mut self,
        request: ReferenceSourceScopeRequest,
        context: &mut Context<'_, Self>,
    ) -> Result<ReferenceSourceScopeResponse, ReferenceControlError> {
        let source_id = request.source_id;
        let scope = request.scope;
        let enabled = request.enabled;
        let domain_scope = domain_source_scope(scope.clone());
        #[cfg(not(test))]
        let result = {
            let scope_subject = source_scope_subject(&scope)?;
            let key = kairos_conflux::ConnectionKey::new(
                crate::services::providers::MassiveOptionsCoverageSource::connection_key(
                    scope_subject,
                )
                .map_err(control_error)?,
            )
            .map_err(|error| control_error(ReferenceError::Provider(error)))?;
            let created = if source_id.as_str() == "massive-options"
                && enabled
                && !self
                    .option_underlyings()
                    .iter()
                    .any(|value| value == scope_subject)
            {
                let (planned_key, parameters) = self
                    .massive_option_connection_plan(scope_subject)
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
                .set_managed_source_scope(
                    source_id.as_str(),
                    domain_scope,
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
            .set_source_scope(
                source_id.as_str(),
                domain_scope,
                enabled,
                &mut context.connections(),
            )
            .await
            .map_err(control_error)?;
        let _ = self.publish_pending_to_outputs(context).await;
        Ok(ReferenceSourceScopeResponse {
            source_id,
            scope,
            enabled,
            generation: result.generation,
            event_sequence: result.event_sequence,
            changed: result.changed,
        })
    }

    #[cfg(test)]
    pub async fn set_source_scope(
        &mut self,
        source_id: &str,
        scope: crate::domain::SourceScope,
        enabled: bool,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<ReferenceRefreshResult> {
        let result = self
            .actor
            .set_source_scope(source_id, scope, enabled, connections)
            .await?;
        Ok(ReferenceRefreshResult {
            generation: result.generation,
            event_sequence: result.event_sequence,
            changed: result.changed,
            change_count: result.event_count,
            events: result.events,
        })
    }

    #[cfg(not(test))]
    pub(crate) fn massive_option_connection_plan(
        &self,
        underlying: &str,
    ) -> ReferenceResult<(
        kairos_conflux::ConnectionKey,
        kairos_conflux::MassiveRestConfig,
    )> {
        self.actor.massive_option_connection_plan(underlying)
    }

    #[cfg(not(test))]
    pub(crate) async fn set_managed_source_scope(
        &mut self,
        source_id: &str,
        scope: crate::domain::SourceScope,
        enabled: bool,
        connection_key: Option<kairos_conflux::ConnectionKey>,
        connections: &mut kairos_conflux::ConnectionCollections<'_>,
    ) -> ReferenceResult<ReferenceRefreshResult> {
        let result = self
            .actor
            .set_managed_source_scope(source_id, scope, enabled, connection_key, connections)
            .await?;
        Ok(ReferenceRefreshResult {
            generation: result.generation,
            event_sequence: result.event_sequence,
            changed: result.changed,
            change_count: result.event_count,
            events: result.events,
        })
    }
}

pub(crate) fn control_error(error: ReferenceError) -> ReferenceControlError {
    ReferenceControlError {
        code: error.code().into(),
        message: error.to_string(),
        retryable: error.retryable(),
        details: std::collections::BTreeMap::new(),
    }
}

fn contract_provider_status(desired_state: SourceDesiredState) -> ReferenceProviderStatus {
    match desired_state {
        SourceDesiredState::Enabled => ReferenceProviderStatus::Ready,
        SourceDesiredState::Paused => ReferenceProviderStatus::Paused,
        SourceDesiredState::Disabled | SourceDesiredState::Removed => {
            ReferenceProviderStatus::Disabled
        },
    }
}

fn domain_source_desired_state(desired_state: ReferenceSourceDesiredState) -> SourceDesiredState {
    match desired_state {
        ReferenceSourceDesiredState::Enabled => SourceDesiredState::Enabled,
        ReferenceSourceDesiredState::Disabled => SourceDesiredState::Disabled,
        ReferenceSourceDesiredState::Paused => SourceDesiredState::Paused,
        ReferenceSourceDesiredState::Removed => SourceDesiredState::Removed,
    }
}

fn domain_source_definition(
    source_id: ProviderId,
    request: ReferenceSourceDefinitionRequest,
) -> ReferenceSourceDefinition {
    ReferenceSourceDefinition {
        source_id,
        provider_id: request.provider_id,
        provider_product: request.provider_product.map(|value| {
            ProviderProductCode::new(contract_provider_product_name(value))
                .expect("contract provider product is valid")
        }),
        scope: domain_source_scope(request.scope),
        desired_state: domain_source_desired_state(request.desired_state),
        credential_binding: request.credential_binding.map(|value| {
            SourceCredentialBinding::new(value).expect("contract credential binding is valid")
        }),
        sync_policy: domain_source_sync_policy(request.sync_policy),
    }
}

fn domain_source_scope(scope: ReferenceSourceScope) -> SourceScope {
    SourceScope {
        kind: match scope.kind {
            ReferenceSourceScopeKind::Global => SourceScopeKind::Global,
            ReferenceSourceScopeKind::ProviderCatalog => SourceScopeKind::ProviderCatalog,
            ReferenceSourceScopeKind::UnderlyingInstrument => SourceScopeKind::UnderlyingInstrument,
            ReferenceSourceScopeKind::Coverage => SourceScopeKind::Coverage,
            ReferenceSourceScopeKind::Custom => SourceScopeKind::Custom,
        },
        id: scope.id,
    }
}

#[cfg(not(test))]
fn source_scope_subject(scope: &ReferenceSourceScope) -> Result<&str, ReferenceControlError> {
    match scope.kind {
        ReferenceSourceScopeKind::UnderlyingInstrument | ReferenceSourceScopeKind::Coverage => {
            scope.id.as_deref().ok_or_else(|| {
                control_error(ReferenceError::Invalid(
                    "source scope requires an id".to_owned(),
                ))
            })
        },
        ReferenceSourceScopeKind::Global
        | ReferenceSourceScopeKind::ProviderCatalog
        | ReferenceSourceScopeKind::Custom => Err(control_error(ReferenceError::Invalid(format!(
            "{} source scope cannot be dynamically connected yet",
            scope.kind.as_str()
        )))),
    }
}

fn domain_source_sync_policy(sync_policy: ReferenceSourceSyncPolicy) -> SourceSyncPolicy {
    match sync_policy {
        ReferenceSourceSyncPolicy::FullSnapshot => SourceSyncPolicy::FullSnapshot,
        ReferenceSourceSyncPolicy::PagedSnapshot => SourceSyncPolicy::PagedSnapshot,
        ReferenceSourceSyncPolicy::ScopedSnapshot => SourceSyncPolicy::ScopedSnapshot,
        ReferenceSourceSyncPolicy::IncrementalDelta => SourceSyncPolicy::IncrementalDelta,
        ReferenceSourceSyncPolicy::ManualCurated => SourceSyncPolicy::ManualCurated,
    }
}

fn contract_provider_product_name(provider_product: ReferenceProviderProduct) -> &'static str {
    match provider_product {
        ReferenceProviderProduct::Spot => "spot",
        ReferenceProviderProduct::Equity => "equity",
        ReferenceProviderProduct::Options => "options",
        ReferenceProviderProduct::Usdm => "usdm",
        ReferenceProviderProduct::Coinm => "coinm",
        ReferenceProviderProduct::Margin => "margin",
        ReferenceProviderProduct::Swap => "swap",
        ReferenceProviderProduct::Futures => "futures",
        ReferenceProviderProduct::Perpetual => "perpetual",
        ReferenceProviderProduct::Unknown => "unknown",
    }
}

#[cfg(test)]
mod tests {
    use kairos_primitives::integration::ProviderId;
    use kairos_reference_contract::{
        ReferenceProviderProduct, ReferenceSourceDefinitionRequest, ReferenceSourceDesiredState,
        ReferenceSourceScope, ReferenceSourceScopeKind, ReferenceSourceSyncPolicy,
    };

    use crate::domain::{SourceDesiredState, SourceScopeKind, SourceSyncPolicy};

    use super::{domain_source_definition, domain_source_desired_state, domain_source_scope};

    #[test]
    fn source_desired_state_contract_mapping_is_bidirectional() {
        [
            (
                SourceDesiredState::Enabled,
                ReferenceSourceDesiredState::Enabled,
            ),
            (
                SourceDesiredState::Disabled,
                ReferenceSourceDesiredState::Disabled,
            ),
            (
                SourceDesiredState::Paused,
                ReferenceSourceDesiredState::Paused,
            ),
            (
                SourceDesiredState::Removed,
                ReferenceSourceDesiredState::Removed,
            ),
        ]
        .into_iter()
        .for_each(|(domain, contract)| {
            assert_eq!(domain_source_desired_state(contract), domain);
        });
    }

    #[test]
    fn source_definition_contract_mapping_preserves_workflow_fields() {
        let source_id = ProviderId::new("massive-options").unwrap();
        let definition = domain_source_definition(
            source_id,
            ReferenceSourceDefinitionRequest {
                source_id: ProviderId::new("massive-options").unwrap(),
                provider_id: ProviderId::new("massive").unwrap(),
                provider_product: Some(ReferenceProviderProduct::Options),
                scope: ReferenceSourceScope {
                    kind: ReferenceSourceScopeKind::UnderlyingInstrument,
                    id: Some("instrument:equity:US:SPY:common".into()),
                },
                desired_state: ReferenceSourceDesiredState::Paused,
                credential_binding: Some("massive.default".into()),
                sync_policy: ReferenceSourceSyncPolicy::ScopedSnapshot,
            },
        );

        assert_eq!(definition.source_id, "massive-options");
        assert_eq!(definition.provider_id, "massive");
        assert_eq!(definition.provider_product.as_deref(), Some("options"));
        assert_eq!(definition.scope.kind, SourceScopeKind::UnderlyingInstrument);
        assert_eq!(
            definition.scope.id.as_deref(),
            Some("instrument:equity:US:SPY:common")
        );
        assert_eq!(definition.desired_state, SourceDesiredState::Paused);
        assert_eq!(
            definition.credential_binding.as_deref(),
            Some("massive.default")
        );
        assert_eq!(definition.sync_policy, SourceSyncPolicy::ScopedSnapshot);
    }

    #[test]
    fn source_scope_contract_mapping_preserves_scope_identity() {
        let scope = domain_source_scope(ReferenceSourceScope {
            kind: ReferenceSourceScopeKind::Coverage,
            id: Some("instrument:equity:US:SPY:common".into()),
        });

        assert_eq!(scope.kind, SourceScopeKind::Coverage);
        assert_eq!(scope.id.as_deref(), Some("instrument:equity:US:SPY:common"));
    }
}
