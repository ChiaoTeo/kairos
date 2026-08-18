//! Concrete Binance connections, organized by product family and transport.

macro_rules! rest_connection {
    ($name:ident, $domain:literal) => {
        pub struct $name {
            service: crate::services::participants::binance::rest::RestService,
        }

        impl $name {
            pub fn new(
                config: crate::participants::binance::BinanceRestConfig,
            ) -> Result<Self, crate::IntegrationError> {
                let descriptor = config.descriptor($domain)?;
                let credential = config.credential;
                Ok(Self {
                    service: crate::services::participants::binance::rest::RestService::new(
                        descriptor,
                        config.endpoint,
                        credential,
                    )?,
                })
            }

            pub fn descriptor(&self) -> &crate::ConnectionDescriptor {
                self.service.descriptor()
            }

            pub fn endpoint(&self) -> &str {
                self.service.endpoint()
            }
        }
    };
}

macro_rules! websocket_connection {
    ($name:ident, $domain:literal) => {
        pub struct $name {
            service: crate::services::participants::binance::socket::SocketService,
            subscriptions: std::collections::BTreeMap<
                crate::MarketSubscriptionId,
                (Vec<crate::MarketFeed>, Vec<String>),
            >,
            pending_market_events:
                crate::transport::websocket::InboundDispatcher<crate::MarketEvent>,
            next_subscription_id: u64,
            next_request_id: u64,
        }

        impl $name {
            pub fn new(
                config: crate::participants::binance::BinanceWebSocketConfig,
            ) -> Result<Self, crate::IntegrationError> {
                let descriptor = config.descriptor($domain)?;
                Ok(Self {
                    service: crate::services::participants::binance::socket::SocketService::new(
                        descriptor,
                        config.endpoint,
                        config.event_capacity,
                    )?,
                    subscriptions: std::collections::BTreeMap::new(),
                    pending_market_events:
                        crate::transport::websocket::InboundDispatcher::new(
                            config.event_capacity,
                        )?,
                    next_subscription_id: 1,
                    next_request_id: 1,
                })
            }

            pub fn descriptor(&self) -> &crate::ConnectionDescriptor {
                self.service.descriptor()
            }

            async fn next_value(&mut self) -> Result<serde_json::Value, crate::IntegrationError> {
                loop {
                    match self.service.next().await? {
                        tokio_tungstenite::tungstenite::Message::Text(text) => {
                            return serde_json::from_str(&text).map_err(|error| {
                                crate::IntegrationError::InvalidPayload(error.to_string())
                            });
                        }
                        tokio_tungstenite::tungstenite::Message::Close(_) => {
                            return Err(crate::IntegrationError::Transport(
                                "Binance WebSocket closed".into(),
                            ));
                        }
                        _ => continue,
                    }
                }
            }

            async fn send_and_confirm(
                &mut self,
                method: &str,
                streams: &[String],
            ) -> Result<(), crate::IntegrationError> {
                let request_id = self.next_request_id;
                self.next_request_id = self.next_request_id.saturating_add(1);
                self.service
                    .send(serde_json::json!({
                        "method": method,
                        "params": streams,
                        "id": request_id,
                    }).to_string())
                    .await?;
                loop {
                    let value = self.next_value().await?;
                    if value.get("id").and_then(serde_json::Value::as_u64) == Some(request_id) {
                        if let Some(error) = value.get("error") {
                            return Err(crate::IntegrationError::InvalidRequest(format!(
                                "Binance subscription rejected: {error}"
                            )));
                        }
                        return Ok(());
                    }
                    self.pending_market_events.extend(
                        crate::services::participants::binance::stream::normalize(&value)?,
                    )?;
                }
            }

            async fn restore_subscriptions(&mut self) -> Result<(), crate::IntegrationError> {
                let streams = self
                    .subscriptions
                    .values()
                    .flat_map(|(_, streams)| streams.clone())
                    .collect::<Vec<_>>();
                if !streams.is_empty() {
                    self.send_and_confirm("SUBSCRIBE", &streams).await?;
                }
                Ok(())
            }
        }

        impl crate::ConnectionHealthQuery for $name {
            fn connection_health(&mut self) -> crate::ConnectionHealth {
                self.service.health()
            }
        }

        impl crate::ConnectionLifecycleCommand for $name {
            async fn connect(&mut self) -> Result<(), crate::IntegrationError> {
                self.service.connect().await?;
                self.restore_subscriptions().await
            }

            async fn disconnect(&mut self) -> Result<(), crate::IntegrationError> {
                self.pending_market_events.clear();
                self.service.disconnect().await
            }

            async fn reconnect(&mut self) -> Result<(), crate::IntegrationError> {
                self.service.reconnect().await?;
                self.restore_subscriptions().await
            }
        }
    };
}

macro_rules! market_websocket_capabilities {
    ($name:ident, $family:literal) => {
        impl crate::MarketSubscriptionCommand for $name {
            async fn subscribe(
                &mut self,
                request: crate::MarketSubscriptionRequest,
            ) -> Result<
                crate::MarketSubscriptionOutcome<crate::MarketSubscription>,
                crate::IntegrationError,
            > {
                let streams = request
                    .feeds
                    .iter()
                    .map(|feed| {
                        crate::services::participants::binance::stream::stream_name(feed, $family)
                    })
                    .collect::<Result<Vec<_>, _>>()?;
                let id = crate::MarketSubscriptionId(self.next_subscription_id);
                self.next_subscription_id = self.next_subscription_id.saturating_add(1);
                let subscription = crate::MarketSubscription {
                    id,
                    feeds: request.feeds.clone(),
                    delivery: crate::MarketDelivery::Push,
                };
                match self.send_and_confirm("SUBSCRIBE", &streams).await {
                    Ok(()) => {
                        self.subscriptions.insert(id, (request.feeds, streams));
                        Ok(crate::MarketSubscriptionOutcome::Confirmed(subscription))
                    }
                    Err(crate::IntegrationError::InvalidRequest(message)) => Ok(
                        crate::MarketSubscriptionOutcome::Rejected(crate::ParticipantRejection {
                            code: None,
                            message,
                            participant_request_id: Some(id.0.to_string()),
                        }),
                    ),
                    Err(crate::IntegrationError::NotReady) => {
                        Err(crate::IntegrationError::NotReady)
                    }
                    Err(error) => {
                        self.subscriptions.insert(id, (request.feeds, streams));
                        Ok(crate::MarketSubscriptionOutcome::Indeterminate {
                            provisional: Some(subscription),
                            reason: error.to_string(),
                        })
                    }
                }
            }

            async fn unsubscribe(
                &mut self,
                subscription: crate::MarketSubscriptionId,
            ) -> Result<crate::MarketSubscriptionOutcome<()>, crate::IntegrationError> {
                let (_, streams) =
                    self.subscriptions
                        .get(&subscription)
                        .cloned()
                        .ok_or_else(|| {
                            crate::IntegrationError::InvalidRequest(
                                "unknown Binance market subscription".into(),
                            )
                        })?;
                match self.send_and_confirm("UNSUBSCRIBE", &streams).await {
                    Ok(()) => {
                        self.subscriptions.remove(&subscription);
                        Ok(crate::MarketSubscriptionOutcome::Confirmed(()))
                    }
                    Err(crate::IntegrationError::InvalidRequest(message)) => Ok(
                        crate::MarketSubscriptionOutcome::Rejected(crate::ParticipantRejection {
                            code: None,
                            message,
                            participant_request_id: Some(subscription.0.to_string()),
                        }),
                    ),
                    Err(crate::IntegrationError::NotReady) => {
                        Err(crate::IntegrationError::NotReady)
                    }
                    Err(error) => Ok(crate::MarketSubscriptionOutcome::Indeterminate {
                        provisional: Some(()),
                        reason: error.to_string(),
                    }),
                }
            }
        }

        impl crate::MarketDataStream for $name {
            async fn next(&mut self) -> Result<crate::MarketEvent, crate::IntegrationError> {
                if let Some(event) = self.pending_market_events.pop() {
                    return Ok(event);
                }
                loop {
                    let value = self.next_value().await?;
                    let mut events =
                        crate::services::participants::binance::stream::normalize(&value)?;
                    let Some(first) = events.pop_front() else {
                        continue;
                    };
                    self.pending_market_events.extend(events)?;
                    return Ok(first);
                }
            }
        }
    };
}

macro_rules! user_websocket_connection {
    ($name:ident, $domain:literal, $listen_key_path:literal) => {
        pub struct $name {
            descriptor: crate::ConnectionDescriptor,
            rest: crate::services::participants::binance::rest::RestService,
            socket: Option<crate::services::participants::binance::socket::SocketService>,
            websocket_endpoint: String,
            segment_key: String,
            event_capacity: usize,
            listen_key: Option<String>,
            keep_alive_at: Option<tokio::time::Instant>,
            channel_epoch: u64,
            pending_accounts: std::collections::VecDeque<crate::ExternalAccountEventEnvelope>,
            pending_executions: std::collections::VecDeque<
                crate::ExternalEventEnvelope<crate::ExternalExecutionEvent>,
            >,
        }

        impl $name {
            pub fn new(
                config: crate::participants::binance::BinanceUserWebSocketConfig,
            ) -> Result<Self, crate::IntegrationError> {
                if config.segment_key.trim().is_empty() {
                    return Err(crate::IntegrationError::InvalidRequest(
                        "Binance user stream segment key is required".into(),
                    ));
                }
                let descriptor = config.descriptor($domain)?;
                let rest = crate::services::participants::binance::rest::RestService::new(
                    crate::ConnectionDescriptor::new(
                        format!("{}.listen-key", descriptor.binding_id),
                        descriptor.participant.clone(),
                        concat!($domain, ".listen-key"),
                    )
                    .map(|mut value| {
                        value.environment = descriptor.environment.clone();
                        value.principal_id = descriptor.principal_id.clone();
                        value
                    })
                    .map_err(crate::IntegrationError::InvalidRequest)?,
                    config.rest_endpoint,
                    Some(config.credential.clone()),
                )?;
                Ok(Self {
                    descriptor,
                    rest,
                    socket: None,
                    websocket_endpoint: config.websocket_endpoint,
                    segment_key: config.segment_key,
                    event_capacity: config.event_capacity,
                    listen_key: None,
                    keep_alive_at: None,
                    channel_epoch: 0,
                    pending_accounts: std::collections::VecDeque::new(),
                    pending_executions: std::collections::VecDeque::new(),
                })
            }

            pub fn descriptor(&self) -> &crate::ConnectionDescriptor {
                &self.descriptor
            }

            fn socket_mut(
                &mut self,
            ) -> Result<
                &mut crate::services::participants::binance::socket::SocketService,
                crate::IntegrationError,
            > {
                self.socket
                    .as_mut()
                    .ok_or(crate::IntegrationError::NotReady)
            }

            async fn receive(&mut self) -> Result<(), crate::IntegrationError> {
                enum Received {
                    Message(tokio_tungstenite::tungstenite::Message),
                    KeepAlive,
                }
                let received = if let Some(deadline) = self.keep_alive_at {
                    let socket = self
                        .socket
                        .as_mut()
                        .ok_or(crate::IntegrationError::NotReady)?;
                    tokio::select! {
                        message = socket.next() => Received::Message(message?),
                        () = tokio::time::sleep_until(deadline) => Received::KeepAlive,
                    }
                } else {
                    Received::Message(self.socket_mut()?.next().await?)
                };
                let message = match received {
                    Received::Message(message) => message,
                    Received::KeepAlive => {
                        let listen_key = self
                            .listen_key
                            .as_deref()
                            .ok_or(crate::IntegrationError::NotReady)?;
                        self.rest
                            .keep_alive_listen_key($listen_key_path, listen_key)
                            .await?;
                        self.keep_alive_at = Some(
                            tokio::time::Instant::now() + std::time::Duration::from_secs(30 * 60),
                        );
                        return Ok(());
                    }
                };
                let tokio_tungstenite::tungstenite::Message::Text(text) = message else {
                    return Ok(());
                };
                let value: serde_json::Value = serde_json::from_str(&text)
                    .map_err(|error| crate::IntegrationError::InvalidPayload(error.to_string()))?;
                let account = crate::services::participants::binance::user::account_event(
                    &self.descriptor.binding_id,
                    &self.segment_key,
                    self.channel_epoch,
                    &value,
                )?;
                let execution = crate::services::participants::binance::user::execution_event(
                    &self.descriptor.binding_id,
                    self.channel_epoch,
                    &value,
                )?;
                let additions = usize::from(account.is_some()) + usize::from(execution.is_some());
                if self.pending_accounts.len() + self.pending_executions.len() + additions
                    > self.event_capacity
                {
                    return Err(crate::IntegrationError::Backpressure(
                        "Binance user-data event buffer overflowed".into(),
                    ));
                }
                self.pending_accounts.extend(account);
                self.pending_executions.extend(execution);
                Ok(())
            }
        }

        impl crate::ConnectionHealthQuery for $name {
            fn connection_health(&mut self) -> crate::ConnectionHealth {
                self.socket.as_mut().map_or(
                    crate::ConnectionHealth {
                        lifecycle: crate::ConnectionLifecycle::Created,
                        healthy: false,
                        authenticated: false,
                        last_error: None,
                    },
                    |socket| socket.health(),
                )
            }
        }

        impl crate::ConnectionLifecycleCommand for $name {
            async fn connect(&mut self) -> Result<(), crate::IntegrationError> {
                let listen_key = self.rest.create_listen_key($listen_key_path).await?;
                let endpoint = if self.websocket_endpoint.contains("{listenKey}") {
                    self.websocket_endpoint.replace("{listenKey}", &listen_key)
                } else if $domain == "advanced.stocks.user.websocket" {
                    format!(
                        "{}/ws/{}@orderReport",
                        self.websocket_endpoint.trim_end_matches('/'),
                        listen_key
                    )
                } else {
                    format!(
                        "{}/{}",
                        self.websocket_endpoint.trim_end_matches('/'),
                        listen_key
                    )
                };
                let mut socket =
                    crate::services::participants::binance::socket::SocketService::new(
                        self.descriptor.clone(),
                        endpoint,
                        self.event_capacity,
                    )?;
                socket.connect().await?;
                self.channel_epoch = self.channel_epoch.saturating_add(1);
                self.listen_key = Some(listen_key);
                self.keep_alive_at =
                    Some(tokio::time::Instant::now() + std::time::Duration::from_secs(30 * 60));
                self.socket = Some(socket);
                Ok(())
            }

            async fn disconnect(&mut self) -> Result<(), crate::IntegrationError> {
                self.pending_accounts.clear();
                self.pending_executions.clear();
                self.listen_key = None;
                self.keep_alive_at = None;
                if let Some(mut socket) = self.socket.take() {
                    socket.disconnect().await?;
                }
                Ok(())
            }

            async fn reconnect(&mut self) -> Result<(), crate::IntegrationError> {
                self.disconnect().await?;
                self.connect().await
            }
        }

        impl crate::AccountStream for $name {
            async fn next(
                &mut self,
            ) -> Result<crate::ExternalAccountEventEnvelope, crate::IntegrationError> {
                loop {
                    if let Some(event) = self.pending_accounts.pop_front() {
                        return Ok(event);
                    }
                    self.receive().await?;
                }
            }
        }

        impl crate::ExecutionStream for $name {
            async fn next(
                &mut self,
            ) -> Result<
                crate::ExternalEventEnvelope<crate::ExternalExecutionEvent>,
                crate::IntegrationError,
            > {
                loop {
                    if let Some(event) = self.pending_executions.pop_front() {
                        return Ok(event);
                    }
                    self.receive().await?;
                }
            }
        }

        impl crate::ParticipantEventStream for $name {
            async fn next(
                &mut self,
            ) -> Result<crate::ExternalParticipantEvent, crate::IntegrationError> {
                loop {
                    if let Some(event) = self.pending_accounts.pop_front() {
                        return Ok(crate::ExternalParticipantEvent::Account(event));
                    }
                    if let Some(event) = self.pending_executions.pop_front() {
                        return Ok(crate::ExternalParticipantEvent::Execution(event));
                    }
                    self.receive().await?;
                }
            }
        }
    };
}

macro_rules! websocket_api_connection {
    ($name:ident, $domain:literal) => {
        pub struct $name {
            service: crate::services::participants::binance::api::ApiService,
        }

        impl $name {
            pub fn new(
                config: crate::participants::binance::BinanceWebSocketConfig,
            ) -> Result<Self, crate::IntegrationError> {
                Ok(Self {
                    service: crate::services::participants::binance::api::ApiService::new(
                        config, $domain,
                    )?,
                })
            }

            pub fn descriptor(&self) -> &crate::ConnectionDescriptor {
                self.service.descriptor()
            }
        }

        impl crate::ConnectionHealthQuery for $name {
            fn connection_health(&mut self) -> crate::ConnectionHealth {
                self.service.health()
            }
        }

        impl crate::ConnectionLifecycleCommand for $name {
            async fn connect(&mut self) -> Result<(), crate::IntegrationError> {
                self.service.connect().await
            }

            async fn disconnect(&mut self) -> Result<(), crate::IntegrationError> {
                self.service.disconnect().await
            }

            async fn reconnect(&mut self) -> Result<(), crate::IntegrationError> {
                self.service.disconnect().await?;
                self.service.connect().await
            }
        }

        impl crate::OrderCommand for $name {
            async fn submit_order(
                &mut self,
                request: &crate::OrderEntryRequest,
            ) -> crate::CommandResult<crate::OrderEntryEvent> {
                let params = crate::services::participants::binance::execution::params(request)?;
                match self.service.request("order.place", params).await {
                    Ok(crate::services::participants::binance::api::ApiReply::Confirmed(value)) => {
                        crate::services::participants::binance::execution::submitted(
                            request, &value,
                        )
                    }
                    Ok(crate::services::participants::binance::api::ApiReply::Rejected(error)) => {
                        Ok(crate::CommandOutcome::Rejected(error))
                    }
                    Err(
                        error @ (crate::IntegrationError::Authentication(_)
                        | crate::IntegrationError::Authorization(_)
                        | crate::IntegrationError::InvalidRequest(_)
                        | crate::IntegrationError::NotReady),
                    ) => Err(error),
                    Err(error) => Ok(crate::CommandOutcome::Indeterminate(
                        crate::IndeterminateCommand::may_have_been_sent(error.to_string()),
                    )),
                }
            }

            async fn cancel_order(
                &mut self,
                request: &crate::OrderEntryRequest,
                remote_order_id: &str,
                at_unix_nanos: u64,
            ) -> crate::CommandResult<crate::OrderEntryEvent> {
                let params = vec![
                    (
                        "symbol",
                        request.participant_instrument.source_symbol.as_str().into(),
                    ),
                    ("orderId", remote_order_id.into()),
                ];
                match self.service.request("order.cancel", params).await {
                    Ok(crate::services::participants::binance::api::ApiReply::Confirmed(value)) => {
                        crate::services::participants::binance::execution::canceled(
                            request,
                            remote_order_id,
                            at_unix_nanos,
                            &value,
                        )
                    }
                    Ok(crate::services::participants::binance::api::ApiReply::Rejected(error)) => {
                        Ok(crate::CommandOutcome::Rejected(error))
                    }
                    Err(
                        error @ (crate::IntegrationError::Authentication(_)
                        | crate::IntegrationError::Authorization(_)
                        | crate::IntegrationError::InvalidRequest(_)
                        | crate::IntegrationError::NotReady),
                    ) => Err(error),
                    Err(error) => Ok(crate::CommandOutcome::Indeterminate(
                        crate::IndeterminateCommand::may_have_been_sent(error.to_string()),
                    )),
                }
            }
        }
    };
}

macro_rules! futures_rest_capabilities {
    ($name:ident, $prefix:literal, $kind:expr) => {
        impl crate::InstrumentCatalogQuery for $name {
            async fn fetch_instruments(
                &mut self,
            ) -> Result<crate::ExternalInstrumentCatalog, crate::IntegrationError> {
                let value = self
                    .service
                    .public_get(concat!($prefix, "/exchangeInfo"), &[])
                    .await?;
                Ok(crate::ExternalInstrumentCatalog {
                    participant: crate::ParticipantRef::new(
                        crate::ParticipantKind::Exchange,
                        "binance",
                    )
                    .expect("static Binance participant"),
                    instruments:
                        crate::services::participants::binance::market::derivative_instruments(
                            &value, $kind,
                        )?,
                })
            }
            async fn fetch_instruments_page(
                &mut self,
                cursor: Option<&str>,
                limit: usize,
            ) -> Result<crate::ExternalInstrumentCatalogPage, crate::IntegrationError> {
                if cursor.is_some() {
                    return Ok(crate::ExternalInstrumentCatalogPage {
                        catalog: crate::ExternalInstrumentCatalog {
                            participant: crate::ParticipantRef::new(
                                crate::ParticipantKind::Exchange,
                                "binance",
                            )
                            .expect("static Binance participant"),
                            instruments: Vec::new(),
                        },
                        next_cursor: None,
                        complete: true,
                    });
                }
                let mut catalog = self.fetch_instruments().await?;
                if limit > 0 {
                    catalog
                        .instruments
                        .truncate(limit.min(catalog.instruments.len()));
                }
                Ok(crate::ExternalInstrumentCatalogPage {
                    catalog,
                    next_cursor: None,
                    complete: true,
                })
            }
        }
        impl crate::MarketQuoteQuery for $name {
            async fn fetch_quotes(
                &mut self,
                symbols: &[kairos_primitives::ParticipantSymbol],
            ) -> Result<Vec<crate::MarketQuote>, crate::IntegrationError> {
                let mut values = Vec::new();
                for symbol in symbols {
                    let value = self
                        .service
                        .public_get(
                            concat!($prefix, "/ticker/bookTicker"),
                            &[("symbol", symbol.as_str().into())],
                        )
                        .await?;
                    values.push(crate::services::participants::binance::market::quote(
                        symbol, &value,
                    )?);
                }
                Ok(values)
            }
        }
        impl crate::MarketTradeQuery for $name {
            async fn fetch_trades(
                &mut self,
                symbols: &[kairos_primitives::ParticipantSymbol],
            ) -> Result<Vec<crate::MarketTrade>, crate::IntegrationError> {
                let mut values = Vec::new();
                for symbol in symbols {
                    let value = self
                        .service
                        .public_get(
                            concat!($prefix, "/trades"),
                            &[("symbol", symbol.as_str().into()), ("limit", "100".into())],
                        )
                        .await?;
                    values.extend(crate::services::participants::binance::market::trades(
                        symbol, &value,
                    )?);
                }
                Ok(values)
            }
        }
        impl crate::MarketBarQuery for $name {
            async fn fetch_bars(
                &mut self,
                request: &crate::MarketBarRequest,
            ) -> Result<Vec<crate::MarketBar>, crate::IntegrationError> {
                let mut values = Vec::new();
                for symbol in &request.symbols {
                    let value = self
                        .service
                        .public_get(
                            concat!($prefix, "/klines"),
                            &[
                                ("symbol", symbol.as_str().into()),
                                ("interval", request.interval.clone()),
                                ("limit", "500".into()),
                            ],
                        )
                        .await?;
                    values.extend(crate::services::participants::binance::market::bars(
                        symbol,
                        &request.interval,
                        &value,
                    )?)
                }
                Ok(values)
            }
        }
        impl crate::MarketOrderBookQuery for $name {
            async fn fetch_order_books(
                &mut self,
                request: &crate::MarketOrderBookRequest,
            ) -> Result<Vec<crate::MarketOrderBook>, crate::IntegrationError> {
                let mut values = Vec::new();
                for symbol in &request.symbols {
                    let value = self
                        .service
                        .public_get(
                            concat!($prefix, "/depth"),
                            &[
                                ("symbol", symbol.as_str().into()),
                                ("limit", request.depth.unwrap_or(100).to_string()),
                            ],
                        )
                        .await?;
                    values.push(crate::services::participants::binance::market::book(
                        symbol, &value,
                    )?)
                }
                Ok(values)
            }
        }
        impl crate::MarketMarkPriceQuery for $name {
            async fn fetch_mark_prices(
                &mut self,
                symbols: &[kairos_primitives::ParticipantSymbol],
            ) -> Result<Vec<crate::MarketMarkPrice>, crate::IntegrationError> {
                let mut values = Vec::new();
                for symbol in symbols {
                    let value = self
                        .service
                        .public_get(
                            concat!($prefix, "/premiumIndex"),
                            &[("symbol", symbol.as_str().into())],
                        )
                        .await?;
                    values.push(crate::services::participants::binance::market::mark_price(
                        symbol, &value,
                    )?)
                }
                Ok(values)
            }
        }
        impl crate::MarketIndexPriceQuery for $name {
            async fn fetch_index_prices(
                &mut self,
                symbols: &[kairos_primitives::ParticipantSymbol],
            ) -> Result<Vec<crate::MarketIndexPrice>, crate::IntegrationError> {
                let mut values = Vec::new();
                for symbol in symbols {
                    let value = self
                        .service
                        .public_get(
                            concat!($prefix, "/premiumIndex"),
                            &[("symbol", symbol.as_str().into())],
                        )
                        .await?;
                    values.push(crate::services::participants::binance::market::index_price(
                        symbol, &value,
                    )?)
                }
                Ok(values)
            }
        }
        impl crate::MarketFundingRateQuery for $name {
            async fn fetch_funding_rates(
                &mut self,
                symbols: &[kairos_primitives::ParticipantSymbol],
            ) -> Result<Vec<crate::MarketFundingRate>, crate::IntegrationError> {
                let mut values = Vec::new();
                for symbol in symbols {
                    let value = self
                        .service
                        .public_get(
                            concat!($prefix, "/premiumIndex"),
                            &[("symbol", symbol.as_str().into())],
                        )
                        .await?;
                    values.push(
                        crate::services::participants::binance::market::funding_rate(
                            symbol, &value,
                        )?,
                    )
                }
                Ok(values)
            }
        }
        impl crate::MarketOpenInterestQuery for $name {
            async fn fetch_open_interest(
                &mut self,
                symbols: &[kairos_primitives::ParticipantSymbol],
            ) -> Result<Vec<crate::MarketOpenInterest>, crate::IntegrationError> {
                let mut values = Vec::new();
                for symbol in symbols {
                    let value = self
                        .service
                        .public_get(
                            concat!($prefix, "/openInterest"),
                            &[("symbol", symbol.as_str().into())],
                        )
                        .await?;
                    values.push(
                        crate::services::participants::binance::market::open_interest(
                            symbol, &value,
                        )?,
                    )
                }
                Ok(values)
            }
        }
        impl crate::AccountQuery for $name {
            async fn fetch_account(
                &mut self,
                segment: &crate::ExternalAccountSegment,
            ) -> Result<crate::ExternalAccountSnapshot, crate::IntegrationError> {
                let value = self
                    .service
                    .signed_get(concat!($prefix, "/account"), &[])
                    .await?;
                crate::services::participants::binance::account::futures(
                    segment,
                    &value,
                    match $kind {
                        crate::ExternalInstrumentKind::Perpetual => "perpetual",
                        crate::ExternalInstrumentKind::Future => "future",
                        _ => "contract",
                    },
                )
            }
        }
        impl crate::OrderCommand for $name {
            async fn submit_order(
                &mut self,
                request: &crate::OrderEntryRequest,
            ) -> crate::CommandResult<crate::OrderEntryEvent> {
                let params = crate::services::participants::binance::execution::params(request)?;
                let outcome = self
                    .service
                    .signed_post_command(concat!($prefix, "/order"), &params)
                    .await?;
                crate::services::participants::binance::execution::submitted_outcome(
                    request, outcome,
                )
            }
            async fn cancel_order(
                &mut self,
                request: &crate::OrderEntryRequest,
                remote_order_id: &str,
                at_unix_nanos: u64,
            ) -> crate::CommandResult<crate::OrderEntryEvent> {
                let params = [
                    (
                        "symbol",
                        request.participant_instrument.source_symbol.as_str().into(),
                    ),
                    ("orderId", remote_order_id.into()),
                ];
                let outcome = self
                    .service
                    .signed_delete_command(concat!($prefix, "/order"), &params)
                    .await?;
                crate::services::participants::binance::execution::canceled_outcome(
                    request,
                    remote_order_id,
                    at_unix_nanos,
                    outcome,
                )
            }
        }
        impl crate::OrderQuery for $name {
            async fn open_orders(
                &mut self,
                query: &crate::ExternalOrderQuery,
            ) -> Result<Vec<crate::ExternalOrder>, crate::IntegrationError> {
                let params = $crate::participants::binance::binance_order_query(query, false)?;
                let value = self
                    .service
                    .signed_get(concat!($prefix, "/openOrders"), &params)
                    .await?;
                crate::services::participants::binance::execution::orders(
                    &self.descriptor().binding_id,
                    &value,
                )
            }
            async fn order_history(
                &mut self,
                query: &crate::ExternalOrderQuery,
            ) -> Result<Vec<crate::ExternalOrder>, crate::IntegrationError> {
                let params = $crate::participants::binance::binance_order_query(query, false)?;
                let value = self
                    .service
                    .signed_get(concat!($prefix, "/allOrders"), &params)
                    .await?;
                crate::services::participants::binance::execution::orders(
                    &self.descriptor().binding_id,
                    &value,
                )
            }
            async fn order_detail(
                &mut self,
                query: &crate::ExternalOrderQuery,
            ) -> Result<Option<crate::ExternalOrder>, crate::IntegrationError> {
                let params = $crate::participants::binance::binance_order_query(query, true)?;
                let value = self
                    .service
                    .signed_get(concat!($prefix, "/order"), &params)
                    .await?;
                Ok(crate::services::participants::binance::execution::orders(
                    &self.descriptor().binding_id,
                    &value,
                )?
                .into_iter()
                .next())
            }
        }
    };
}

fn binance_order_query(
    query: &crate::ExternalOrderQuery,
    detail: bool,
) -> Result<Vec<(&'static str, String)>, crate::IntegrationError> {
    let mut values = Vec::new();
    if let Some(symbol) = &query.symbol {
        values.push(("symbol", symbol.to_string()));
    } else {
        return Err(crate::IntegrationError::InvalidRequest(
            "Binance order query requires symbol".into(),
        ));
    }
    if detail {
        if let Some(id) = &query.order_id {
            values.push(("origClientOrderId", id.to_string()));
        } else {
            return Err(crate::IntegrationError::InvalidRequest(
                "Binance order detail requires order id".into(),
            ));
        }
    }
    if let Some(limit) = query.limit {
        values.push(("limit", limit.to_string()));
    }
    Ok(values)
}

pub mod advanced;
pub mod coinm;
mod config;
pub mod funding;
pub mod margin;
pub mod options;
pub mod spot;
pub mod usdm;

pub use config::{
    BinanceCredential, BinanceRestConfig, BinanceUserWebSocketConfig, BinanceWebSocketConfig,
};
