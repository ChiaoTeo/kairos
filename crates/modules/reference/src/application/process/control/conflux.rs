//! Conflux actor and JSON-RPC control adapter.

use std::convert::Infallible;

use kairos_conflux::{ConfluxActor, ConfluxEvent, Context, SystemEvent};
use kairos_primitives::reference::{InstrumentId, ReferenceSourceId};
use kairos_protocol::control::jsonrpc::{ErrorObjectOwned, RpcResult, business_error};
use kairos_reference_contract::{
    ReferenceControlError, ReferenceHealthResponse, ReferenceMutationResponse,
    ReferenceOptionCoverageResponse, ReferencePublishResponse, ReferenceRefreshResponse,
    ReferenceRuntimeStatusResponse, ReferenceSourceControlRequest,
    ReferenceSourceDefinitionRequest, ReferenceSourceScopeRequest, ReferenceSourceScopeResponse,
    ReferenceSourceStatusResponse, UpsertAssetRequest, UpsertInstrumentRequest,
    UpsertListingRequest,
};

use super::{ReferenceApplication, ReferenceRpcActor, ReferenceTickTrigger};
use crate::domain::{ReferenceError, SourceDesiredState};

const REFERENCE_BUSINESS_ERROR_CODE: i32 = -31_001;

impl ConfluxActor for ReferenceApplication {
    type FatalError = ReferenceError;
    type LocalEvent = Infallible;

    async fn started(&mut self, context: &mut Context<'_, Self>) -> Result<(), Self::FatalError> {
        self.start_runtime(context).await
    }

    async fn handle(
        &mut self,
        event: ConfluxEvent,
        context: &mut Context<'_, Self>,
    ) -> Result<(), Self::FatalError> {
        match event {
            ConfluxEvent::System(SystemEvent::Timer { name, .. }) if name == "refresh" => {
                self.advance_timer_tick(context).await?;
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

    async fn status(
        &mut self,
        (): (),
        _context: &mut Context<'_, Self>,
    ) -> RpcResult<ReferenceRuntimeStatusResponse> {
        Ok(self.contract_runtime_status().await)
    }

    async fn refresh(
        &mut self,
        source_id: Option<ReferenceSourceId>,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<ReferenceRefreshResponse> {
        let result = match source_id.as_ref() {
            Some(source_id) => {
                self.advance_source_with_trigger(
                    source_id.as_str(),
                    &mut context.connections(),
                    ReferenceTickTrigger::Rpc,
                )
                .await
            },
            None => {
                self.advance_sources_with_trigger(
                    &mut context.connections(),
                    ReferenceTickTrigger::Rpc,
                )
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

    async fn set_source_desired_state(
        &mut self,
        request: ReferenceSourceControlRequest,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<ReferenceSourceStatusResponse> {
        self.set_source_desired_state_from_contract(request, context)
            .await
            .map_err(rpc_control_error)
    }

    async fn upsert_source_definition(
        &mut self,
        request: ReferenceSourceDefinitionRequest,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<ReferenceSourceStatusResponse> {
        self.upsert_source_definition_from_contract(request, context)
            .await
            .map_err(rpc_reference_error)
    }

    async fn set_source_scope(
        &mut self,
        request: ReferenceSourceScopeRequest,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<ReferenceSourceScopeResponse> {
        self.change_source_scope(request, context)
            .await
            .map_err(rpc_control_error)
    }

    async fn pause_source(
        &mut self,
        source_id: ReferenceSourceId,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<ReferenceSourceStatusResponse> {
        self.set_source_desired_state_response(source_id, SourceDesiredState::Paused, context)
            .await
            .map_err(rpc_control_error)
    }

    async fn resume_source(
        &mut self,
        source_id: ReferenceSourceId,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<ReferenceSourceStatusResponse> {
        self.set_source_desired_state_response(source_id, SourceDesiredState::Enabled, context)
            .await
            .map_err(rpc_control_error)
    }

    async fn disable_source(
        &mut self,
        source_id: ReferenceSourceId,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<ReferenceSourceStatusResponse> {
        self.set_source_desired_state_response(source_id, SourceDesiredState::Disabled, context)
            .await
            .map_err(rpc_control_error)
    }

    async fn enable_source(
        &mut self,
        source_id: ReferenceSourceId,
        context: &mut Context<'_, Self>,
    ) -> RpcResult<ReferenceSourceStatusResponse> {
        self.set_source_desired_state_response(source_id, SourceDesiredState::Enabled, context)
            .await
            .map_err(rpc_control_error)
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
        self.publish_pending_to_outputs(context).await
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
