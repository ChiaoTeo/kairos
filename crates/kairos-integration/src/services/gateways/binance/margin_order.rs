//! Native Binance margin order-entry connection.

use std::collections::BTreeMap;

use crate::application::{Connection, ConnectionSpec, OrderEntryConnection};
use crate::domain::{
    AccessScope, IntegrationCapability, OrderEntryEvent, OrderEntryRequest, ProductFamily,
    TransportKind,
};
use crate::services::connections::ManagedConnection;

use super::spot::account::BinanceSpotAccountClient;
use super::spot::order;

pub struct BinanceMarginOrderConnection {
    connection: ManagedConnection,
    client: BinanceSpotAccountClient,
    product: ProductFamily,
}

impl BinanceMarginOrderConnection {
    pub fn new(
        product: ProductFamily,
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, String> {
        if !matches!(
            product,
            ProductFamily::CrossMargin | ProductFamily::IsolatedMargin
        ) {
            return Err("Binance margin order entry requires cross or isolated margin".into());
        }
        let client = BinanceSpotAccountClient::new(api_key, secret, base_url)
            .map_err(|error| error.to_string())?;
        let connection = ManagedConnection::new(
            ConnectionSpec {
                connection_id: format!("execution.binance.{}.rest", product_name(product)),
                route: crate::domain::IntegrationRoute::exchange("binance"),
                product: Some(product),
                access: AccessScope::Private,
                transport: TransportKind::Rest,
                capability: IntegrationCapability::OrderEntry,
                credential_id: Some("binance".into()),
                asset_type: None,
            },
            Vec::new(),
        )?;
        Ok(Self {
            connection,
            client,
            product,
        })
    }
}

impl Connection for BinanceMarginOrderConnection {
    fn identity(&self) -> &crate::domain::ConnectionIdentity {
        self.connection.identity()
    }
    fn state(&self) -> &crate::domain::ConnectionState {
        self.connection.state()
    }
    fn start(&mut self) -> Result<(), String> {
        self.connection.start()
    }
    fn stop(&mut self) -> Result<(), String> {
        self.connection.stop()
    }
    fn reconnect(&mut self) -> Result<(), String> {
        self.connection.reconnect()
    }
    fn health(&self) -> crate::domain::ConnectionHealth {
        self.connection.health()
    }
}

impl OrderEntryConnection for BinanceMarginOrderConnection {
    fn submit_order(&mut self, request: &OrderEntryRequest) -> Result<OrderEntryEvent, String> {
        self.start()?;
        if request.options.post_only == Some(true)
            && request.order_type != crate::domain::OrderType::Limit
        {
            return Err("Binance Margin post-only orders require a limit order".into());
        }
        let mut params = BTreeMap::from([
            ("symbol".into(), order::symbol(request)?),
            ("side".into(), order::side(request.side).into()),
            ("type".into(), order::order_type(request).into()),
            ("quantity".into(), order::format_decimal(request.quantity)),
            ("newClientOrderId".into(), request.order_id.clone()),
            ("newOrderRespType".into(), "RESULT".into()),
        ]);
        if self.product == ProductFamily::IsolatedMargin {
            params.insert("isIsolated".into(), "TRUE".into());
        }
        if let (crate::domain::OrderType::Limit, Some(price)) =
            (request.order_type, request.limit_price)
        {
            params.insert("price".into(), order::format_decimal(price));
            params.insert(
                "timeInForce".into(),
                match request.options.time_in_force {
                    Some(crate::domain::TimeInForce::ImmediateOrCancel) => "IOC",
                    Some(crate::domain::TimeInForce::FillOrKill) => "FOK",
                    _ => "GTC",
                }
                .into(),
            );
        }
        let payload = self
            .client
            .signed_post("/sapi/v1/margin/order", params)
            .map_err(|error| error.to_string())?;
        order::normalize_order_event(request, &payload)
    }

    fn cancel_order(
        &mut self,
        request: &OrderEntryRequest,
        venue_order_id: &str,
        _at_unix_nanos: u64,
    ) -> Result<OrderEntryEvent, String> {
        self.start()?;
        if venue_order_id.trim().is_empty() {
            return Err("venue order id is required for cancellation".into());
        }
        let mut params = BTreeMap::from([
            ("symbol".into(), order::symbol(request)?),
            ("orderId".into(), venue_order_id.into()),
        ]);
        if self.product == ProductFamily::IsolatedMargin {
            params.insert("isIsolated".into(), "TRUE".into());
        }
        let payload = self
            .client
            .signed_delete("/sapi/v1/margin/order", params)
            .map_err(|error| error.to_string())?;
        order::normalize_order_event(request, &payload)
    }
}

fn product_name(product: ProductFamily) -> &'static str {
    match product {
        ProductFamily::CrossMargin => "cross-margin",
        ProductFamily::IsolatedMargin => "isolated-margin",
        _ => "margin",
    }
}
