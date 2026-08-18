// Routing behavior tests live outside the production module boundary.

mod tests {
    use super::super::{ExecutionRoute, RoutedAsyncOrderEntry, RoutedAsyncOrderQuery};
    use kairos_integration::{
        CommandOutcome, ConnectionDescriptor, ExternalOrder, ExternalOrderQuery, IntegrationError,
        OrderCommand, OrderQuery, ParticipantInstrumentTypeRef,
    };
    use kairos_integration::{
        DecimalValue, OrderEntryEvent, OrderEntryOptions, OrderEntryRequest, OrderEntryStatus,
        OrderSide, OrderStatus, OrderType, ParticipantInstrumentRef, ParticipantKind,
        ParticipantRef,
    };
    use std::sync::{Arc, Mutex};

    struct RecordingEntry {
        id: &'static str,
        calls: Arc<Mutex<Vec<&'static str>>>,
    }

    impl OrderCommand for RecordingEntry {
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
                order_id: kairos_primitives::OrderId::new(self.order_id).unwrap(),
                client_order_id: None,
                symbol: kairos_primitives::Symbol::new("BTCUSDT").unwrap(),
                side: OrderSide::Buy,
                order_type: OrderType::Limit,
                status: OrderStatus::Acknowledged,
                quantity: DecimalValue::new(1, 0),
                filled_quantity: DecimalValue::new(0, 0),
                average_fill_price: None,
                occurred_at_unix_nanos: None,
            }
        }
    }

    impl OrderQuery for FixtureQuery {
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
            domain: kairos_integration::ConnectionDomainRef::new("spot").unwrap(),
        }
    }

    fn request(account: &str, participant: &str) -> OrderEntryRequest {
        OrderEntryRequest {
            order_id: kairos_primitives::OrderId::new(format!("{participant}-order")).unwrap(),
            intent_id: None,
            account_id: kairos_primitives::AccountId::new(account).unwrap(),
            segment_key: kairos_primitives::SegmentKey::new("spot").unwrap(),
            instrument_id: kairos_primitives::InstrumentId::new("btc-usdt").unwrap(),
            market_id: None,
            participant_instrument: ParticipantInstrumentRef::new(
                ParticipantRef::new(ParticipantKind::Exchange, participant).unwrap(),
                Some(ParticipantInstrumentTypeRef::new(format!("{participant}-spot")).unwrap()),
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
        request.participant_instrument.instrument_type = instrument_type
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
            kairos_primitives::AccountId::new(account_id).unwrap(),
            kairos_primitives::SegmentKey::new("spot").unwrap(),
            Some(ParticipantInstrumentTypeRef::new(format!("{participant}-spot")).unwrap()),
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
            kairos_primitives::AccountId::new("main").unwrap(),
            kairos_primitives::SegmentKey::new("derivatives").unwrap(),
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
        request.segment_key = kairos_primitives::SegmentKey::new("derivatives").unwrap();
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
                instrument_type: None,
                order_id: Some(kairos_primitives::OrderId::new("same-id").unwrap()),
                ..ExternalOrderQuery::default()
            })
            .await
            .unwrap()
            .unwrap();
        assert_eq!(one.binding_id, "execution.okx.hedge");
    }
}
