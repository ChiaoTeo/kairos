//! Execution-owned selection of concrete Integration capabilities.
//!
//! Integration defines provider connections and normalized requests/facts.
//! Execution owns which provider/principal binding serves one business
//! account and segment. This module deliberately is not a provider port or a
//! global registry: it is a private, typed collection with current callers in
//! Execution composition and runtime.

use kairos_domain_types::{AccountId, SegmentKey};
use kairos_integration::application::{
    AsyncOrderEntryConnection, AsyncOrderQueryConnection, CommandOutcome, ConnectionDescriptor,
    ExternalOrder, ExternalOrderQuery, IntegrationError, ParticipantInstrumentTypeRef,
};
use kairos_integration::application::{OrderEntryEvent, OrderEntryRequest};

/// One business route bound to one concrete Integration capability.
pub(crate) struct ExecutionRoute<C> {
    pub(crate) route_id: String,
    pub(crate) account_id: AccountId,
    pub(crate) segment_key: SegmentKey,
    /// Exact participant-owned product discriminator for this route. It is
    /// deliberately opaque to Execution: composition maps provider-native
    /// types into this value and routing only compares identity.
    pub(crate) provider_instrument_type: Option<ParticipantInstrumentTypeRef>,
    pub(crate) descriptor: ConnectionDescriptor,
    pub(crate) connection: C,
}

impl<C> ExecutionRoute<C> {
    pub(crate) fn new(
        route_id: impl Into<String>,
        account_id: AccountId,
        segment_key: SegmentKey,
        provider_instrument_type: Option<ParticipantInstrumentTypeRef>,
        descriptor: ConnectionDescriptor,
        connection: C,
    ) -> Result<Self, String> {
        let route_id = route_id.into();
        if route_id.trim().is_empty() {
            return Err("execution route_id is required".into());
        }
        descriptor.validate()?;
        Ok(Self {
            route_id,
            account_id,
            segment_key,
            provider_instrument_type,
            descriptor,
            connection,
        })
    }

    fn matches_order(&self, request: &OrderEntryRequest) -> bool {
        self.account_id == request.account_id
            && self.segment_key == request.segment_key
            && self.descriptor.participant == request.provider_instrument.participant
            && self.provider_instrument_type == request.provider_instrument.instrument_type
    }
}

fn validate_routes<C>(routes: &[ExecutionRoute<C>]) -> Result<(), String> {
    if routes.is_empty() {
        return Err("at least one execution route is required".into());
    }
    for (index, route) in routes.iter().enumerate() {
        for other in &routes[index + 1..] {
            if route.route_id == other.route_id {
                return Err(format!("duplicate execution route_id: {}", route.route_id));
            }
            if route.descriptor.binding_id == other.descriptor.binding_id {
                return Err(format!(
                    "duplicate Integration binding_id in Execution routes: {}",
                    route.descriptor.binding_id
                ));
            }
            if route.account_id == other.account_id
                && route.segment_key == other.segment_key
                && route.descriptor.participant == other.descriptor.participant
                && route.provider_instrument_type == other.provider_instrument_type
            {
                return Err(format!(
                    "ambiguous Execution route for account={}, segment={}, participant={}",
                    route.account_id, route.segment_key, route.descriptor.participant.id
                ));
            }
        }
    }
    Ok(())
}

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
mod tests {
    use super::{ExecutionRoute, RoutedAsyncOrderEntry, RoutedAsyncOrderQuery};
    use kairos_integration::application::{
        AsyncOrderEntryConnection, AsyncOrderQueryConnection, CommandOutcome, ConnectionDescriptor,
        ExternalOrder, ExternalOrderQuery, IntegrationError, ParticipantInstrumentTypeRef,
    };
    use kairos_integration::application::{
        DecimalValue, OrderEntryEvent, OrderEntryOptions, OrderEntryRequest, OrderEntryStatus,
        OrderSide, OrderStatus, OrderType, ParticipantKind, ParticipantRef, ProviderInstrumentRef,
    };
    use std::sync::{Arc, Mutex};

    struct RecordingEntry {
        id: &'static str,
        calls: Arc<Mutex<Vec<&'static str>>>,
    }

    impl AsyncOrderEntryConnection for RecordingEntry {
        async fn submit_order(
            &mut self,
            request: &OrderEntryRequest,
        ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
            self.calls.lock().unwrap().push(self.id);
            Ok(CommandOutcome::Confirmed(OrderEntryEvent {
                order_id: request.order_id.clone(),
                status: OrderEntryStatus::Accepted,
                remote_order_id: None,
                filled_quantity: None,
                occurred_at_unix_nanos: 1.into(),
                reason: String::new(),
            }))
        }

        async fn cancel_order(
            &mut self,
            request: &OrderEntryRequest,
            _remote_order_id: &str,
            _at_unix_nanos: u64,
        ) -> Result<CommandOutcome<OrderEntryEvent>, IntegrationError> {
            self.submit_order(request).await
        }
    }

    struct FixtureQuery {
        order_id: &'static str,
        detail: bool,
    }

    impl FixtureQuery {
        fn order(&self) -> ExternalOrder {
            ExternalOrder {
                binding_id: String::new(),
                order_id: kairos_domain_types::OrderId::new(self.order_id).unwrap(),
                client_order_id: None,
                symbol: kairos_domain_types::Symbol::new("BTCUSDT").unwrap(),
                side: OrderSide::Buy,
                order_type: OrderType::Limit,
                status: OrderStatus::Acknowledged,
                quantity: DecimalValue::new(1, 0),
                filled_quantity: DecimalValue::new(0, 0),
                average_fill_price: None,
                occurred_at_unix_millis: None,
            }
        }
    }

    impl AsyncOrderQueryConnection for FixtureQuery {
        async fn open_orders(
            &mut self,
            _query: &ExternalOrderQuery,
        ) -> Result<Vec<ExternalOrder>, IntegrationError> {
            Ok(vec![self.order()])
        }

        async fn order_history(
            &mut self,
            _query: &ExternalOrderQuery,
        ) -> Result<Vec<ExternalOrder>, IntegrationError> {
            Ok(vec![self.order()])
        }

        async fn order_detail(
            &mut self,
            _query: &ExternalOrderQuery,
        ) -> Result<Option<ExternalOrder>, IntegrationError> {
            Ok(self.detail.then(|| self.order()))
        }
    }

    fn descriptor(binding_id: &str, participant: &str) -> ConnectionDescriptor {
        ConnectionDescriptor {
            binding_id: binding_id.into(),
            participant: ParticipantRef::new(ParticipantKind::Exchange, participant).unwrap(),
            environment: "test".into(),
            principal_id: Some(binding_id.into()),
            domain: kairos_integration::application::ConnectionDomainRef::new("spot").unwrap(),
        }
    }

    fn request(account: &str, participant: &str) -> OrderEntryRequest {
        OrderEntryRequest {
            order_id: kairos_domain_types::OrderId::new(format!("{participant}-order")).unwrap(),
            intent_id: None,
            account_id: kairos_domain_types::AccountId::new(account).unwrap(),
            segment_key: kairos_domain_types::SegmentKey::new("spot").unwrap(),
            instrument_id: kairos_domain_types::InstrumentId::new("btc-usdt").unwrap(),
            market_id: None,
            provider_instrument: ProviderInstrumentRef::new(
                ParticipantRef::new(ParticipantKind::Exchange, participant).unwrap(),
                Some(kairos_integration::participants::binance::ConnectionDomain::Spot.into()),
                "BTCUSDT",
            )
            .unwrap(),
            side: OrderSide::Buy,
            quantity: DecimalValue::new(1, 0),
            order_type: OrderType::Limit,
            limit_price: Some(DecimalValue::new(10, 0)),
            options: OrderEntryOptions::default(),
        }
    }

    fn request_with_instrument_type(
        account: &str,
        participant: &str,
        instrument_type: Option<&str>,
    ) -> OrderEntryRequest {
        let mut request = request(account, participant);
        request.provider_instrument.instrument_type = instrument_type
            .map(ParticipantInstrumentTypeRef::new)
            .transpose()
            .unwrap();
        request
    }

    fn route<C>(
        route_id: &str,
        binding_id: &str,
        account_id: &str,
        participant: &str,
        connection: C,
    ) -> ExecutionRoute<C> {
        ExecutionRoute::new(
            route_id,
            kairos_domain_types::AccountId::new(account_id).unwrap(),
            kairos_domain_types::SegmentKey::new("spot").unwrap(),
            Some(ParticipantInstrumentTypeRef::new("spot").unwrap()),
            descriptor(binding_id, participant),
            connection,
        )
        .unwrap()
    }

    #[tokio::test]
    async fn entry_selects_by_account_segment_and_participant() {
        let calls = Arc::new(Mutex::new(Vec::new()));
        let mut router = RoutedAsyncOrderEntry::new(vec![
            route(
                "binance-main",
                "execution.binance.main",
                "main",
                "binance",
                RecordingEntry {
                    id: "binance-main",
                    calls: Arc::clone(&calls),
                },
            ),
            route(
                "okx-hedge",
                "execution.okx.hedge",
                "hedge",
                "okx",
                RecordingEntry {
                    id: "okx-hedge",
                    calls: Arc::clone(&calls),
                },
            ),
        ])
        .unwrap();

        router.submit_order(&request("hedge", "okx")).await.unwrap();
        router
            .submit_order(&request("main", "binance"))
            .await
            .unwrap();
        assert_eq!(*calls.lock().unwrap(), ["okx-hedge", "binance-main"]);
    }

    #[tokio::test]
    async fn entry_requires_the_exact_participant_product_discriminator() {
        let calls = Arc::new(Mutex::new(Vec::new()));
        let route = ExecutionRoute::new(
            "binance-usdm",
            kairos_domain_types::AccountId::new("main").unwrap(),
            kairos_domain_types::SegmentKey::new("derivatives").unwrap(),
            Some(ParticipantInstrumentTypeRef::new("usd-m-futures").unwrap()),
            descriptor("execution.binance.usdm", "binance"),
            RecordingEntry {
                id: "binance-usdm",
                calls,
            },
        )
        .unwrap();
        let mut router = RoutedAsyncOrderEntry::new(vec![route]).unwrap();

        let mut request = request_with_instrument_type("main", "binance", Some("swap"));
        request.segment_key = kairos_domain_types::SegmentKey::new("derivatives").unwrap();
        let error = router.submit_order(&request).await.unwrap_err();

        assert!(matches!(error, IntegrationError::InvalidRequest(_)));
    }

    #[tokio::test]
    async fn entry_does_not_treat_a_missing_product_as_a_wildcard() {
        let calls = Arc::new(Mutex::new(Vec::new()));
        let mut router = RoutedAsyncOrderEntry::new(vec![route(
            "binance-main",
            "execution.binance.main",
            "main",
            "binance",
            RecordingEntry {
                id: "binance-main",
                calls,
            },
        )])
        .unwrap();

        let request = request_with_instrument_type("main", "binance", None);
        let error = router.submit_order(&request).await.unwrap_err();

        assert!(matches!(error, IntegrationError::InvalidRequest(_)));
    }

    #[test]
    fn duplicate_dispatch_key_is_rejected_at_composition() {
        let calls = Arc::new(Mutex::new(Vec::new()));
        let result = RoutedAsyncOrderEntry::new(vec![
            route(
                "route-a",
                "binding-a",
                "main",
                "binance",
                RecordingEntry {
                    id: "a",
                    calls: Arc::clone(&calls),
                },
            ),
            route(
                "route-b",
                "binding-b",
                "main",
                "binance",
                RecordingEntry { id: "b", calls },
            ),
        ]);
        let error = match result {
            Ok(_) => panic!("ambiguous routes must be rejected"),
            Err(error) => error,
        };
        assert!(error.contains("ambiguous Execution route"));
    }

    #[tokio::test]
    async fn query_fans_out_and_stamps_binding_identity() {
        let mut router = RoutedAsyncOrderQuery::new(vec![
            route(
                "binance-main",
                "execution.binance.main",
                "main",
                "binance",
                FixtureQuery {
                    order_id: "same-id",
                    detail: false,
                },
            ),
            route(
                "okx-hedge",
                "execution.okx.hedge",
                "hedge",
                "okx",
                FixtureQuery {
                    order_id: "same-id",
                    detail: true,
                },
            ),
        ])
        .unwrap();

        let all = router
            .open_orders(&ExternalOrderQuery::default())
            .await
            .unwrap();
        assert_eq!(all.len(), 2);
        assert_eq!(all[0].binding_id, "execution.binance.main");
        assert_eq!(all[1].binding_id, "execution.okx.hedge");

        let one = router
            .order_detail(&ExternalOrderQuery {
                binding_id: Some("execution.okx.hedge".into()),
                order_id: Some(kairos_domain_types::OrderId::new("same-id").unwrap()),
                ..ExternalOrderQuery::default()
            })
            .await
            .unwrap()
            .unwrap();
        assert_eq!(one.binding_id, "execution.okx.hedge");
    }
}
