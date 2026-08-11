//! Order entry, query, and execution-event capabilities.

mod entry {
    use super::super::execution_facts::{OrderEntryEvent, OrderEntryRequest};
    use crate::application::CommandResult;
    use std::future::Future;

    /// Order-entry capability for a provider connection.
    /// Stateless HTTP commands deliberately have no session lifecycle.
    pub trait OrderEntryConnection: Send {
        fn submit_order(&mut self, request: &OrderEntryRequest) -> CommandResult<OrderEntryEvent>;
        fn cancel_order(
            &mut self,
            request: &OrderEntryRequest,
            remote_order_id: &str,
            at_unix_nanos: u64,
        ) -> CommandResult<OrderEntryEvent>;
    }

    /// Async-first order-entry capability. The explicit `Send` future bound lets
    /// business runtimes place provider work on `tokio::spawn` without boxed
    /// futures.
    pub trait AsyncOrderEntryConnection: Send {
        fn submit_order(
            &mut self,
            request: &OrderEntryRequest,
        ) -> impl Future<Output = CommandResult<OrderEntryEvent>> + Send;
        fn cancel_order(
            &mut self,
            request: &OrderEntryRequest,
            remote_order_id: &str,
            at_unix_nanos: u64,
        ) -> impl Future<Output = CommandResult<OrderEntryEvent>> + Send;
    }
}

pub use entry::*;

mod query {
    //! Provider-neutral remote order queries.

    use super::super::execution_facts::{DecimalValue, OrderSide, OrderStatus, OrderType};
    use crate::application::IntegrationError;
    use kairos_domain_types::{ClientOrderId, OrderId, Symbol, UnixNanos};
    use std::future::Future;

    #[derive(Clone, Debug, Default, Eq, PartialEq)]
    pub struct ExternalOrderQuery {
        /// Select one already-bound provider/principal connection. `None` means
        /// the business-owned route collection may fan the query out.
        pub binding_id: Option<String>,
        pub symbol: Option<Symbol>,
        pub order_id: Option<OrderId>,
        pub limit: Option<u32>,
        pub since_unix_millis: Option<UnixNanos>,
    }

    #[derive(Clone, Debug, Eq, PartialEq)]
    pub struct ExternalOrder {
        /// Technical Integration binding that produced this external fact.
        /// Provider adapters may leave it empty only on legacy compatibility
        /// paths; a business-owned multi-route adapter must stamp it.
        pub binding_id: String,
        pub order_id: OrderId,
        pub client_order_id: Option<ClientOrderId>,
        pub symbol: Symbol,
        pub side: OrderSide,
        pub order_type: OrderType,
        pub status: OrderStatus,
        pub quantity: DecimalValue,
        pub filled_quantity: DecimalValue,
        pub average_fill_price: Option<DecimalValue>,
        pub occurred_at_unix_millis: Option<UnixNanos>,
    }

    pub trait OrderQueryConnection: Send {
        fn open_orders(
            &mut self,
            query: &ExternalOrderQuery,
        ) -> Result<Vec<ExternalOrder>, IntegrationError>;
        fn order_history(
            &mut self,
            query: &ExternalOrderQuery,
        ) -> Result<Vec<ExternalOrder>, IntegrationError>;
        fn order_detail(
            &mut self,
            query: &ExternalOrderQuery,
        ) -> Result<Option<ExternalOrder>, IntegrationError>;
    }

    pub trait AsyncOrderQueryConnection: Send {
        fn open_orders(
            &mut self,
            query: &ExternalOrderQuery,
        ) -> impl Future<Output = Result<Vec<ExternalOrder>, IntegrationError>> + Send;
        fn order_history(
            &mut self,
            query: &ExternalOrderQuery,
        ) -> impl Future<Output = Result<Vec<ExternalOrder>, IntegrationError>> + Send;
        fn order_detail(
            &mut self,
            query: &ExternalOrderQuery,
        ) -> impl Future<Output = Result<Option<ExternalOrder>, IntegrationError>> + Send;
    }
}

pub use query::*;

mod events {
    //! Provider-neutral execution/order update stream capability.

    use super::super::execution_facts::{DecimalValue, OrderSide, OrderType};
    use crate::application::{ExternalEventEnvelope, IntegrationError};
    use crate::domain::ConnectionHealth;
    use kairos_domain_types::{Currency, FillId, OrderId, OrderStatus, Symbol, UnixNanos};
    use std::future::Future;

    #[derive(Clone, Debug, Eq, PartialEq)]
    pub struct ExternalExecutionEvent {
        pub order_id: OrderId,
        pub symbol: Symbol,
        pub status: OrderStatus,
        pub side: Option<OrderSide>,
        pub order_type: Option<OrderType>,
        pub quantity: Option<DecimalValue>,
        pub limit_price: Option<DecimalValue>,
        pub filled_quantity: Option<DecimalValue>,
        pub remaining_quantity: Option<DecimalValue>,
        pub fill_quantity: Option<DecimalValue>,
        pub fill_price: Option<DecimalValue>,
        pub execution_id: Option<FillId>,
        pub fee_currency: Option<Currency>,
        pub fee_amount: Option<DecimalValue>,
        pub occurred_at_unix_nanos: UnixNanos,
        pub reason: String,
    }

    /// Stateful provider channel for external order/fill facts.
    ///
    /// Lifecycle is capability-specific; stateless REST operations have no
    /// lifecycle to inherit.
    pub trait OrderEventSource: Send {
        fn connect_channel(&mut self) -> Result<(), IntegrationError>;
        fn disconnect_channel(&mut self) -> Result<(), IntegrationError>;
        fn reconnect_channel(&mut self) -> Result<(), IntegrationError>;
        fn channel_health(&self) -> ConnectionHealth;
        fn try_next_order_event(
            &mut self,
        ) -> Result<Option<ExternalEventEnvelope<ExternalExecutionEvent>>, IntegrationError>;
    }

    /// Async-first provider channel for external order/fill facts.
    ///
    /// The returned futures are driven wherever the caller awaits or spawns them;
    /// the connection does not store or require a Tokio `Runtime`/`Handle`.
    /// Native async traits are intentional here; heterogeneous routes are
    /// represented by business-owned concrete enums, not by a capability
    /// registry.
    pub trait AsyncOrderEventSource: Send {
        fn connect_channel(&mut self) -> impl Future<Output = Result<(), IntegrationError>> + Send;
        fn disconnect_channel(
            &mut self,
        ) -> impl Future<Output = Result<(), IntegrationError>> + Send;

        fn reconnect_channel(
            &mut self,
        ) -> impl Future<Output = Result<(), IntegrationError>> + Send {
            async move {
                self.disconnect_channel().await?;
                self.connect_channel().await
            }
        }

        fn channel_health(&self) -> ConnectionHealth;

        /// Await the next normalized provider event. Non-order messages and
        /// heartbeats are consumed internally; absence is not represented as an
        /// `Option` and therefore cannot invite busy polling.
        fn next_order_event(
            &mut self,
        ) -> impl Future<
            Output = Result<ExternalEventEnvelope<ExternalExecutionEvent>, IntegrationError>,
        > + Send;
    }
}

pub use events::*;
