//! Concrete Binance connections, organized by product family and transport.

macro_rules! rest_connection {
    ($name:ident, $domain:literal) => {
        pub struct $name {
            service: crate::services::participants::binance::rest::RestService,
        }

        impl $name {
            pub fn new(
                connection_key: crate::ConnectionKey,
                config: crate::participants::binance::BinanceRestConfig,
            ) -> Result<Self, crate::IntegrationError> {
                let descriptor = config.descriptor(connection_key, $domain)?;
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

            pub fn rate_limit_headers(&self) -> std::collections::BTreeMap<String, String> {
                self.service.rate_limit_headers()
            }

            pub fn clock_health(&self) -> crate::ProviderClockHealth {
                self.service.clock_health()
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
            order_book_sequences: crate::services::sequence::OrderBookSequenceTracker,
            next_subscription_id: u64,
            next_request_id: u64,
        }

        impl $name {
            pub fn new(
                connection_key: crate::ConnectionKey,
                config: crate::participants::binance::BinanceWebSocketConfig,
            ) -> Result<Self, crate::IntegrationError> {
                let descriptor = config.descriptor(connection_key, $domain)?;
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
                    order_book_sequences: Default::default(),
                    next_subscription_id: 1,
                    next_request_id: 1,
                })
            }

            pub fn descriptor(&self) -> &crate::ConnectionDescriptor {
                self.service.descriptor()
            }

            /// Seed the last update id from a REST depth snapshot before
            /// draining buffered Binance depth events.
            pub fn seed_order_book_sequence(
                &mut self,
                symbol: &kairos_primitives::ParticipantSymbol,
                last_update_id: u64,
            ) {
                self.order_book_sequences
                    .seed(symbol.as_str(), last_update_id);
            }

            fn queue_market_events(
                &mut self,
                events: impl IntoIterator<Item = crate::MarketEvent>,
            ) -> Result<(), crate::IntegrationError> {
                for event in events {
                    match self.order_book_sequences.validate_binance(&event)? {
                        crate::services::sequence::SequenceDisposition::Accept => {
                            self.pending_market_events.buffer(event)?;
                        }
                        crate::services::sequence::SequenceDisposition::Duplicate => {}
                    }
                }
                Ok(())
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

            fn poll_next_value(
                &mut self,
                cx: &mut std::task::Context<'_>,
            ) -> std::task::Poll<Result<serde_json::Value, crate::IntegrationError>> {
                loop {
                    let message = match self.service.poll_next(cx) {
                        std::task::Poll::Ready(Ok(message)) => message,
                        std::task::Poll::Ready(Err(error)) => {
                            return std::task::Poll::Ready(Err(error))
                        }
                        std::task::Poll::Pending => return std::task::Poll::Pending,
                    };
                    match message {
                        tokio_tungstenite::tungstenite::Message::Text(text) => {
                            return std::task::Poll::Ready(
                                serde_json::from_str(&text).map_err(|error| {
                                    crate::IntegrationError::InvalidPayload(error.to_string())
                                }),
                            )
                        }
                        tokio_tungstenite::tungstenite::Message::Close(_) => {
                            return std::task::Poll::Ready(Err(
                                crate::IntegrationError::Transport(
                                    "Binance WebSocket closed".into(),
                                ),
                            ))
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
                    self.queue_market_events(
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
                self.order_book_sequences.clear();
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
            fn poll_next(
                &mut self,
                cx: &mut std::task::Context<'_>,
            ) -> std::task::Poll<Result<crate::MarketEvent, crate::IntegrationError>> {
                if let Some(event) = self.pending_market_events.pop() {
                    return std::task::Poll::Ready(Ok(event));
                }
                loop {
                    let value = match self.poll_next_value(cx) {
                        std::task::Poll::Ready(Ok(value)) => value,
                        std::task::Poll::Ready(Err(error)) => {
                            return std::task::Poll::Ready(Err(error))
                        }
                        std::task::Poll::Pending => return std::task::Poll::Pending,
                    };
                    let mut events =
                        match crate::services::participants::binance::stream::normalize(&value) {
                            Ok(events) => events,
                            Err(error) => return std::task::Poll::Ready(Err(error)),
                        };
                    if let Err(error) = self.queue_market_events(events.drain(..)) {
                        return std::task::Poll::Ready(Err(error));
                    }
                    if let Some(event) = self.pending_market_events.pop() {
                        return std::task::Poll::Ready(Ok(event));
                    }
                }
            }
        }

        impl crate::ConnectionMaintenance for $name {
            fn next_maintenance_at(&self) -> Option<tokio::time::Instant> {
                self.service.next_maintenance_at()
            }

            fn poll_maintenance(
                &mut self,
                _cx: &mut std::task::Context<'_>,
                now: tokio::time::Instant,
            ) -> std::task::Poll<Result<crate::MaintenanceOutcome, crate::IntegrationError>> {
                self.service.poll_maintenance(now)
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
            maintenance_future: Option<
                std::pin::Pin<
                    Box<
                        dyn std::future::Future<
                                Output = Result<(), crate::IntegrationError>,
                            > + Send,
                    >,
                >,
            >,
            channel_epoch: u64,
            pending_accounts: std::collections::VecDeque<crate::ExternalAccountEventEnvelope>,
            pending_executions: std::collections::VecDeque<
                crate::ExternalEventEnvelope<crate::ExternalExecutionEvent>,
            >,
        }

        impl $name {
            pub fn new(
                connection_key: crate::ConnectionKey,
                config: crate::participants::binance::BinanceUserWebSocketConfig,
            ) -> Result<Self, crate::IntegrationError> {
                if config.segment_key.trim().is_empty() {
                    return Err(crate::IntegrationError::InvalidRequest(
                        "Binance user stream segment key is required".into(),
                    ));
                }
                let descriptor = config.descriptor(connection_key, $domain)?;
                let rest = crate::services::participants::binance::rest::RestService::new(
                    crate::ConnectionDescriptor::new(
                        format!("{}.listen-key", descriptor.connection_key),
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
                    maintenance_future: None,
                    channel_epoch: 0,
                    pending_accounts: std::collections::VecDeque::new(),
                    pending_executions: std::collections::VecDeque::new(),
                })
            }

            pub fn descriptor(&self) -> &crate::ConnectionDescriptor {
                &self.descriptor
            }

            fn poll_receive(
                &mut self,
                cx: &mut std::task::Context<'_>,
            ) -> std::task::Poll<Result<(), crate::IntegrationError>> {
                let socket = match self.socket.as_mut() {
                    Some(socket) => socket,
                    None => {
                        return std::task::Poll::Ready(Err(
                            crate::IntegrationError::NotReady,
                        ))
                    }
                };
                let message = match socket.poll_next(cx) {
                    std::task::Poll::Ready(Ok(message)) => message,
                    std::task::Poll::Ready(Err(error)) => {
                        return std::task::Poll::Ready(Err(error))
                    }
                    std::task::Poll::Pending => return std::task::Poll::Pending,
                };
                let tokio_tungstenite::tungstenite::Message::Text(text) = message else {
                    return std::task::Poll::Ready(Ok(()));
                };
                let value: serde_json::Value = match serde_json::from_str(&text) {
                    Ok(value) => value,
                    Err(error) => {
                        return std::task::Poll::Ready(Err(
                            crate::IntegrationError::InvalidPayload(error.to_string()),
                        ))
                    }
                };
                let account = match crate::services::participants::binance::user::account_event(
                    &self.descriptor.connection_key,
                    &self.segment_key,
                    self.channel_epoch,
                    &value,
                ) {
                    Ok(value) => value,
                    Err(error) => return std::task::Poll::Ready(Err(error)),
                };
                let execution = match crate::services::participants::binance::user::execution_event(
                    &self.descriptor.connection_key,
                    self.channel_epoch,
                    &value,
                ) {
                    Ok(value) => value,
                    Err(error) => return std::task::Poll::Ready(Err(error)),
                };
                let additions = usize::from(account.is_some()) + usize::from(execution.is_some());
                if self.pending_accounts.len() + self.pending_executions.len() + additions
                    > self.event_capacity
                {
                    return std::task::Poll::Ready(Err(crate::IntegrationError::Backpressure(
                        "Binance user-data event buffer overflowed".into(),
                    )));
                }
                self.pending_accounts.extend(account);
                self.pending_executions.extend(execution);
                std::task::Poll::Ready(Ok(()))
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
                self.maintenance_future = None;
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

        impl crate::ConnectionMaintenance for $name {
            fn next_maintenance_at(&self) -> Option<tokio::time::Instant> {
                [
                    self.keep_alive_at,
                    self.socket.as_ref().and_then(|socket| socket.next_maintenance_at()),
                ]
                .into_iter()
                .flatten()
                .min()
            }

            fn poll_maintenance(
                &mut self,
                cx: &mut std::task::Context<'_>,
                now: tokio::time::Instant,
            ) -> std::task::Poll<
                Result<crate::MaintenanceOutcome, crate::IntegrationError>,
            > {
                if let Some(socket) = self.socket.as_ref() {
                    if socket.next_maintenance_at().is_some_and(|deadline| deadline <= now) {
                        return socket.poll_maintenance(now);
                    }
                }
                let Some(deadline) = self.keep_alive_at else {
                    return std::task::Poll::Ready(Ok(crate::MaintenanceOutcome::Healthy));
                };
                if now < deadline && self.maintenance_future.is_none() {
                    return std::task::Poll::Pending;
                }
                if self.maintenance_future.is_none() {
                    let listen_key = match self.listen_key.clone() {
                        Some(listen_key) => listen_key,
                        None => {
                            return std::task::Poll::Ready(Err(
                                crate::IntegrationError::NotReady,
                            ))
                        }
                    };
                    self.maintenance_future = Some(
                        self.rest
                            .keep_alive_listen_key_future($listen_key_path, listen_key)?,
                    );
                }
                let future = self
                    .maintenance_future
                    .as_mut()
                    .expect("maintenance future initialized");
                match std::future::Future::poll(future.as_mut(), cx) {
                    std::task::Poll::Ready(Ok(())) => {
                        self.maintenance_future = None;
                        self.keep_alive_at = Some(
                            now + std::time::Duration::from_secs(30 * 60),
                        );
                        std::task::Poll::Ready(Ok(crate::MaintenanceOutcome::Progressed))
                    }
                    std::task::Poll::Ready(Err(error)) => {
                        self.maintenance_future = None;
                        std::task::Poll::Ready(Err(error))
                    }
                    std::task::Poll::Pending => std::task::Poll::Pending,
                }
            }
        }

        impl crate::AccountStream for $name {
            fn poll_next(
                &mut self,
                cx: &mut std::task::Context<'_>,
            ) -> std::task::Poll<Result<crate::ExternalAccountEventEnvelope, crate::IntegrationError>> {
                loop {
                    if let Some(event) = self.pending_accounts.pop_front() {
                        return std::task::Poll::Ready(Ok(event));
                    }
                    match self.poll_receive(cx) {
                        std::task::Poll::Ready(Ok(())) => {}
                        std::task::Poll::Ready(Err(error)) => {
                            return std::task::Poll::Ready(Err(error))
                        }
                        std::task::Poll::Pending => return std::task::Poll::Pending,
                    }
                }
            }
        }

        impl crate::ExecutionStream for $name {
            fn poll_next(
                &mut self,
                cx: &mut std::task::Context<'_>,
            ) -> std::task::Poll<Result<
                crate::ExternalEventEnvelope<crate::ExternalExecutionEvent>,
                crate::IntegrationError,
            >> {
                loop {
                    if let Some(event) = self.pending_executions.pop_front() {
                        return std::task::Poll::Ready(Ok(event));
                    }
                    match self.poll_receive(cx) {
                        std::task::Poll::Ready(Ok(())) => {}
                        std::task::Poll::Ready(Err(error)) => {
                            return std::task::Poll::Ready(Err(error))
                        }
                        std::task::Poll::Pending => return std::task::Poll::Pending,
                    }
                }
            }
        }

        impl crate::ParticipantEventStream for $name {
            fn poll_next(
                &mut self,
                cx: &mut std::task::Context<'_>,
            ) -> std::task::Poll<Result<crate::ExternalParticipantEvent, crate::IntegrationError>> {
                loop {
                    if let Some(event) = self.pending_accounts.pop_front() {
                        return std::task::Poll::Ready(Ok(crate::ExternalParticipantEvent::Account(event)));
                    }
                    if let Some(event) = self.pending_executions.pop_front() {
                        return std::task::Poll::Ready(Ok(crate::ExternalParticipantEvent::Execution(event)));
                    }
                    match self.poll_receive(cx) {
                        std::task::Poll::Ready(Ok(())) => {}
                        std::task::Poll::Ready(Err(error)) => {
                            return std::task::Poll::Ready(Err(error))
                        }
                        std::task::Poll::Pending => return std::task::Poll::Pending,
                    }
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
                connection_key: crate::ConnectionKey,
                config: crate::participants::binance::BinanceWebSocketConfig,
            ) -> Result<Self, crate::IntegrationError> {
                Ok(Self {
                    service: crate::services::participants::binance::api::ApiService::new(
                        connection_key,
                        config,
                        $domain,
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
                    &self.descriptor().connection_key,
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
                    &self.descriptor().connection_key,
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
                    &self.descriptor().connection_key,
                    &value,
                )?
                .into_iter()
                .next())
            }
        }
    };
}

macro_rules! futures_native_order_extensions {
    ($name:ident, $prefix:literal) => {
        impl $name {
            pub async fn amend_order(
                &mut self,
                request: &crate::participants::binance::BinanceAmendOrderRequest,
            ) -> crate::CommandResult<crate::OrderEntryEvent> {
                let params =
                    crate::services::participants::binance::execution::amend_params(request)?;
                let outcome = self
                    .service
                    .signed_put_command(concat!($prefix, "/order"), &params)
                    .await?;
                crate::services::participants::binance::execution::submitted_outcome(
                    &request.replacement,
                    outcome,
                )
            }

            pub async fn submit_orders(
                &mut self,
                requests: &[crate::OrderEntryRequest],
            ) -> crate::CommandResult<Vec<crate::CommandOutcome<crate::OrderEntryEvent>>> {
                let batch =
                    crate::services::participants::binance::execution::batch_order_parameter(
                        requests,
                    )?;
                let outcome = self
                    .service
                    .signed_post_command(
                        concat!($prefix, "/batchOrders"),
                        &[("batchOrders", batch)],
                    )
                    .await?;
                crate::services::participants::binance::execution::submitted_batch_outcome(
                    requests, outcome,
                )
            }

            pub async fn cancel_orders(
                &mut self,
                requests: &[crate::participants::binance::BinanceCancelOrderRequest],
            ) -> crate::CommandResult<Vec<crate::CommandOutcome<crate::OrderEntryEvent>>> {
                let first = requests.first().ok_or_else(|| {
                    crate::IntegrationError::InvalidRequest(
                        "Binance Futures cancel batch cannot be empty".into(),
                    )
                })?;
                let symbol = first.order.participant_instrument.source_symbol.as_str();
                if requests.iter().any(|request| {
                    request.order.participant_instrument.source_symbol.as_str() != symbol
                }) {
                    return Err(crate::IntegrationError::InvalidRequest(
                        "Binance Futures cancel batch must use one symbol".into(),
                    ));
                }
                let ids = crate::services::participants::binance::execution::cancel_id_parameter(
                    requests,
                )?;
                let outcome = self
                    .service
                    .signed_delete_command(
                        concat!($prefix, "/batchOrders"),
                        &[("symbol", symbol.into()), ("orderIdList", ids)],
                    )
                    .await?;
                crate::services::participants::binance::execution::canceled_batch_outcome(
                    requests, outcome,
                )
            }

            pub async fn cancel_all_open_orders(
                &mut self,
                scope: &crate::participants::binance::BinanceCancelAllScope,
            ) -> crate::CommandResult<crate::participants::binance::BinanceCancelAllScope> {
                match self
                    .service
                    .signed_delete_command(
                        concat!($prefix, "/allOpenOrders"),
                        &[("symbol", scope.symbol.to_string())],
                    )
                    .await?
                {
                    crate::CommandOutcome::Confirmed(_) => {
                        Ok(crate::CommandOutcome::Confirmed(scope.clone()))
                    }
                    crate::CommandOutcome::Rejected(error) => {
                        Ok(crate::CommandOutcome::Rejected(error))
                    }
                    crate::CommandOutcome::Indeterminate(error) => {
                        Ok(crate::CommandOutcome::Indeterminate(error))
                    }
                }
            }

            pub async fn fetch_account_trades(
                &mut self,
                query: &crate::participants::binance::BinanceHistoryQuery,
            ) -> Result<
                Vec<crate::participants::binance::BinanceTradeRecord>,
                crate::IntegrationError,
            > {
                let payload = self
                    .service
                    .signed_get(concat!($prefix, "/userTrades"), &query.params(true, false)?)
                    .await?;
                crate::participants::binance::history::trades(&payload)
            }

            pub async fn fetch_income_history(
                &mut self,
                query: &crate::participants::binance::BinanceHistoryQuery,
            ) -> Result<
                Vec<crate::participants::binance::BinanceIncomeRecord>,
                crate::IntegrationError,
            > {
                let payload = self
                    .service
                    .signed_get(concat!($prefix, "/income"), &query.params(false, true)?)
                    .await?;
                crate::participants::binance::history::income(&payload)
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
mod history;
pub mod margin;
pub mod options;
mod order;
pub mod spot;
pub mod usdm;

pub use config::{
    BinanceCredential, BinanceRestConfig, BinanceUserWebSocketConfig, BinanceWebSocketConfig,
};
pub use history::{BinanceHistoryQuery, BinanceIncomeRecord, BinanceTradeRecord};
pub use order::{BinanceAmendOrderRequest, BinanceCancelAllScope, BinanceCancelOrderRequest};
