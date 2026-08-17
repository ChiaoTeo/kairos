//! Execution-owned selection of concrete Integration capabilities.
//!
//! Integration defines provider connections and normalized requests/facts.
//! Execution owns which provider/principal binding serves one business
//! account and segment. This module deliberately is not a provider port or a
//! global registry: it is a private, typed collection with current callers in
//! Execution composition and runtime.

use kairos_integration::application::{
    AsyncOrderEntryConnection, AsyncOrderQueryConnection, CommandOutcome, ConnectionDescriptor,
    ExternalOrder, ExternalOrderQuery, IntegrationError, ParticipantInstrumentTypeRef,
};
use kairos_integration::application::{OrderEntryEvent, OrderEntryRequest};
use kairos_primitives::{AccountId, SegmentKey};

mod route;
mod validation;

pub(crate) use route::ExecutionRoute;
use validation::validate_routes;

/// Routes an order command by business account/segment and the explicit
/// provider instrument participant. Each element remains a concrete
/// Integration capability selected by Execution composition.
pub(crate) struct RoutedAsyncOrderEntry<C> {
    routes: Vec<ExecutionRoute<C>>,
}

impl<C> RoutedAsyncOrderEntry<C> {
    pub(crate) fn new(routes: Vec<ExecutionRoute<C>>) -> Result<Self, String> {
        validate_routes(&routes)?;
        Ok(Self { routes })
    }

    fn select_mut(
        &mut self,
        request: &OrderEntryRequest,
    ) -> Result<&mut ExecutionRoute<C>, IntegrationError> {
        let mut matches = self
            .routes
            .iter_mut()
            .filter(|route| route.matches_order(request));
        let route = matches.next().ok_or_else(|| {
            IntegrationError::InvalidRequest(format!(
                "no Execution route for account={}, segment={}, participant={}",
                request.account_id, request.segment_key, request.provider_instrument.participant.id
            ))
        })?;
        debug_assert!(
            matches.next().is_none(),
            "route validation prevents ambiguity"
        );
        Ok(route)
    }
}

impl<C> AsyncOrderEntryConnection for RoutedAsyncOrderEntry<C>
where
    C: AsyncOrderEntryConnection,
{
    async fn submit_order(
        &mut self,
        request: &OrderEntryRequest,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        let route = self.select_mut(request)?;
        tracing::debug!(
            event = "execution_route_selected",
            route_id = %route.route_id,
            binding_id = %route.descriptor.binding_id,
            account_id = %route.account_id,
            segment_key = %route.segment_key,
            "selected Execution order-entry route"
        );
        route.connection.submit_order(request).await
    }

    async fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
        self.select_mut(request)?
            .connection
            .cancel_order(request, remote_order_id, at_unix_nanos)
            .await
    }
}

/// Query collection for all configured Execution bindings. A caller may
/// select one technical binding; an omitted selector fans open/history out to
/// every route. Results are stamped so provider order IDs remain scoped.
pub(crate) struct RoutedAsyncOrderQuery<C> {
    routes: Vec<ExecutionRoute<C>>,
}

impl<C> RoutedAsyncOrderQuery<C> {
    pub(crate) fn new(routes: Vec<ExecutionRoute<C>>) -> Result<Self, String> {
        validate_routes(&routes)?;
        Ok(Self { routes })
    }

    fn selected_indices(&self, query: &ExternalOrderQuery) -> Result<Vec<usize>, IntegrationError> {
        if let Some(binding_id) = query.binding_id.as_deref() {
            let index = self
                .routes
                .iter()
                .position(|route| route.descriptor.binding_id == binding_id)
                .ok_or_else(|| {
                    IntegrationError::InvalidRequest(format!(
                        "Execution query binding is not configured: {binding_id}"
                    ))
                })?;
            Ok(vec![index])
        } else {
            Ok((0..self.routes.len()).collect())
        }
    }
}

fn stamp_orders(
    binding_id: &str,
    mut orders: Vec<ExternalOrder>,
) -> Result<Vec<ExternalOrder>, IntegrationError> {
    for order in &mut orders {
        if !order.binding_id.is_empty() && order.binding_id != binding_id {
            return Err(IntegrationError::InvalidPayload(format!(
                "order query returned binding_id={} through binding_id={binding_id}",
                order.binding_id
            )));
        }
        order.binding_id = binding_id.to_owned();
    }
    Ok(orders)
}

impl<C> AsyncOrderQueryConnection for RoutedAsyncOrderQuery<C>
where
    C: AsyncOrderQueryConnection,
{
    async fn open_orders(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let indices = self.selected_indices(query)?;
        let mut result = Vec::new();
        for index in indices {
            let route = &mut self.routes[index];
            let orders = route.connection.open_orders(query).await?;
            result.extend(stamp_orders(&route.descriptor.binding_id, orders)?);
        }
        Ok(result)
    }

    async fn order_history(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Vec<ExternalOrder>, IntegrationError> {
        let indices = self.selected_indices(query)?;
        let mut result = Vec::new();
        for index in indices {
            let route = &mut self.routes[index];
            let orders = route.connection.order_history(query).await?;
            result.extend(stamp_orders(&route.descriptor.binding_id, orders)?);
        }
        Ok(result)
    }

    async fn order_detail(
        &mut self,
        query: &ExternalOrderQuery,
    ) -> Result<Option<ExternalOrder>, IntegrationError> {
        let indices = self.selected_indices(query)?;
        let mut found: Option<ExternalOrder> = None;
        for index in indices {
            let route = &mut self.routes[index];
            let Some(order) = route.connection.order_detail(query).await? else {
                continue;
            };
            let mut stamped = stamp_orders(&route.descriptor.binding_id, vec![order])?;
            let order = stamped.pop().expect("one stamped order");
            if let Some(previous) = &found {
                return Err(IntegrationError::InvalidPayload(format!(
                    "order detail is ambiguous across bindings {} and {}; specify binding_id",
                    previous.binding_id, order.binding_id
                )));
            }
            found = Some(order);
        }
        Ok(found)
    }
}

#[cfg(test)]
mod tests;
