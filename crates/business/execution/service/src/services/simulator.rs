//! Deterministic order simulation for backtest and paper execution.
//!
//! This module owns simulated order lifecycle and fill generation. It does
//! not mutate Account state; generated fills are handed to Account settlement
//! by the composition/application layer.

use std::collections::BTreeMap;

use crate::application::market_input::{Bar, MarketObservation, Quote};
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};

use crate::domain::{OrderSide, OrderType};
use kairos_domain_types::{
    Currency, FillId, InstrumentId, MarketId, Money, OrderId, Price, Quantity, Rate, UnixNanos,
};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SimulationConfig {
    #[serde(default)]
    pub fee_bps: Rate,
    /// Currency used to pay simulated fees. It is intentionally explicit;
    /// the simulator must not infer it from the settlement asset.
    #[serde(default)]
    pub fee_currency: Option<Currency>,
    #[serde(default)]
    pub slippage_bps: Rate,
    #[serde(default = "default_true")]
    pub enforce_quote_quantity: bool,
}

impl Default for SimulationConfig {
    fn default() -> Self {
        Self {
            fee_bps: Rate::ZERO,
            fee_currency: None,
            slippage_bps: Rate::ZERO,
            enforce_quote_quantity: true,
        }
    }
}

impl SimulationConfig {
    fn validate(&self) -> Result<(), String> {
        if self.fee_bps < Rate::ZERO {
            return Err("simulation fee_bps must be non-negative".into());
        }
        if self.fee_bps > Rate::ZERO && self.fee_currency.is_none() {
            return Err("simulation fee_currency is required when fee_bps is non-zero".into());
        }
        if self.slippage_bps < Rate::ZERO {
            return Err("simulation slippage_bps must be non-negative".into());
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SimulationOrderRequest {
    pub order_id: OrderId,
    pub instrument_id: InstrumentId,
    #[serde(default)]
    pub market_id: Option<MarketId>,
    pub side: OrderSide,
    pub order_type: OrderType,
    pub quantity: Quantity,
    #[serde(default)]
    pub limit_price: Option<Price>,
    pub submitted_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum SimulationOrderStatus {
    Accepted,
    PartiallyFilled,
    Filled,
    Canceled,
    Rejected,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SimulationOrder {
    pub request: SimulationOrderRequest,
    pub status: SimulationOrderStatus,
    pub filled_quantity: Quantity,
    pub remaining_quantity: Quantity,
    pub updated_at_unix_nanos: UnixNanos,
    pub reason: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SimulationFill {
    pub fill_id: FillId,
    pub order_id: OrderId,
    pub instrument_id: InstrumentId,
    #[serde(default)]
    pub execution_market_id: Option<MarketId>,
    pub side: OrderSide,
    pub quantity: Quantity,
    pub price: Price,
    pub fee: Money,
    #[serde(default)]
    pub fee_currency: Option<Currency>,
    pub occurred_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct SimulationResult {
    pub orders: Vec<SimulationOrder>,
    pub fills: Vec<SimulationFill>,
}

#[derive(Clone, Debug)]
struct WorkingOrder {
    order: SimulationOrder,
    quantity: Decimal,
    filled_quantity: Decimal,
    limit_price: Option<Decimal>,
}

pub struct ExecutionSimulator {
    config: SimulationConfig,
    orders: BTreeMap<String, WorkingOrder>,
    fills: Vec<SimulationFill>,
    next_fill_id: u64,
    last_market_event_time: Option<UnixNanos>,
}

impl ExecutionSimulator {
    pub fn new(config: SimulationConfig) -> Result<Self, String> {
        config.validate()?;
        Ok(Self {
            config,
            orders: BTreeMap::new(),
            fills: Vec::new(),
            next_fill_id: 1,
            last_market_event_time: None,
        })
    }

    pub fn business_time(&self) -> Option<UnixNanos> {
        self.last_market_event_time
    }

    pub fn set_business_time(&mut self, value: UnixNanos) {
        self.last_market_event_time = Some(value);
    }

    pub fn submit(&mut self, request: SimulationOrderRequest) -> Result<SimulationOrder, String> {
        if request.order_id.as_str().trim().is_empty() {
            return Err("simulation order_id is required".into());
        }
        if request.instrument_id.as_str().trim().is_empty() {
            return Err("simulation instrument_id is required".into());
        }
        if self.orders.contains_key(request.order_id.as_str()) {
            return Err(format!(
                "simulation order already exists: {}",
                request.order_id
            ));
        }
        let quantity = positive_number(&request.quantity.to_string(), "quantity")?;
        let limit_price = match request.limit_price {
            Some(value) => Some(positive_number(&value.to_string(), "limit_price")?),
            None if request.order_type == OrderType::Limit => {
                return Err("limit order requires limit_price".into())
            }
            None => None,
        };
        let order = SimulationOrder {
            updated_at_unix_nanos: request.submitted_at_unix_nanos,
            request: request.clone(),
            status: SimulationOrderStatus::Accepted,
            filled_quantity: Quantity::new(0, request.quantity.scale())
                .map_err(|error| error.to_string())?,
            remaining_quantity: request.quantity,
            reason: String::new(),
        };
        self.orders.insert(
            request.order_id.to_string(),
            WorkingOrder {
                order: order.clone(),
                quantity,
                filled_quantity: Decimal::ZERO,
                limit_price,
            },
        );
        Ok(order)
    }

    pub fn cancel(
        &mut self,
        order_id: &str,
        at_unix_nanos: UnixNanos,
    ) -> Result<SimulationOrder, String> {
        let working = self
            .orders
            .get_mut(order_id)
            .ok_or_else(|| format!("simulation order not found: {order_id}"))?;
        if matches!(
            working.order.status,
            SimulationOrderStatus::Filled
                | SimulationOrderStatus::Canceled
                | SimulationOrderStatus::Rejected
        ) {
            return Ok(working.order.clone());
        }
        working.order.status = SimulationOrderStatus::Canceled;
        working.order.updated_at_unix_nanos = at_unix_nanos;
        working.order.reason = "canceled by request".into();
        Ok(working.order.clone())
    }

    pub fn apply_market_event(&mut self, event: MarketObservation) -> Result<(), String> {
        let quote = match event {
            MarketObservation::Quote(quote) => quote,
            MarketObservation::Bar(bar) => quote_from_bar(&bar),
            MarketObservation::TradeBar(value) => quote_from_bar(&value.bar),
            MarketObservation::QuoteBar(value) => quote_from_bar(&value.bar),
            _ => return Ok(()),
        };
        self.validate_quote(&quote)?;
        self.try_fill_quote(&quote)
    }

    pub fn order(&self, order_id: &str) -> Option<SimulationOrder> {
        self.orders.get(order_id).map(|value| value.order.clone())
    }

    pub fn orders(&self) -> Vec<SimulationOrder> {
        self.orders
            .values()
            .map(|value| value.order.clone())
            .collect()
    }

    pub fn take_fills(&mut self) -> Vec<SimulationFill> {
        std::mem::take(&mut self.fills)
    }

    pub fn result(&mut self) -> SimulationResult {
        SimulationResult {
            orders: self.orders(),
            fills: self.take_fills(),
        }
    }

    fn validate_quote(&self, quote: &Quote) -> Result<(), String> {
        if quote.market_id.trim().is_empty()
            || quote.instrument_id.trim().is_empty()
            || quote.source_id.trim().is_empty()
        {
            return Err("quote market, instrument and source identities are required".into());
        }
        if quote.bid_price.is_none() && quote.ask_price.is_none() {
            return Err("quote must contain bid_price or ask_price".into());
        }
        Ok(())
    }

    fn try_fill_quote(&mut self, quote: &Quote) -> Result<(), String> {
        let order_ids: Vec<String> = self
            .orders
            .iter()
            .filter(|(_, value)| {
                value.order.request.instrument_id == quote.instrument_id
                    && matches!(
                        value.order.status,
                        SimulationOrderStatus::Accepted | SimulationOrderStatus::PartiallyFilled
                    )
            })
            .map(|(id, _)| id.clone())
            .collect();
        for order_id in order_ids {
            self.try_fill_order(&order_id, quote)?;
        }
        Ok(())
    }

    fn try_fill_order(&mut self, order_id: &str, quote: &Quote) -> Result<(), String> {
        let working = self
            .orders
            .get(order_id)
            .ok_or_else(|| format!("simulation order not found: {order_id}"))?
            .clone();
        if quote.observed_at_unix_nanos < working.order.request.submitted_at_unix_nanos.get() {
            return Ok(());
        }
        let (raw_price, available_quantity) = match working.order.request.side {
            OrderSide::Buy => (quote.ask_price.as_deref(), quote.ask_quantity.as_deref()),
            OrderSide::Sell => (quote.bid_price.as_deref(), quote.bid_quantity.as_deref()),
        };
        let Some(raw_price) = raw_price else {
            return Ok(());
        };
        let price = positive_number(raw_price, "quote price")?;
        if let Some(limit_price) = working.limit_price {
            let executable = match working.order.request.side {
                OrderSide::Buy => price <= limit_price,
                OrderSide::Sell => price >= limit_price,
            };
            if !executable {
                return Ok(());
            }
        }
        let mut quantity = working.quantity - working.filled_quantity;
        if self.config.enforce_quote_quantity {
            if let Some(value) = available_quantity {
                quantity = quantity.min(positive_number(value, "quote quantity")?);
            }
        }
        if quantity <= Decimal::ZERO {
            return Ok(());
        }
        let fill_price = if working.order.request.order_type == OrderType::Market {
            let impact = decimal(self.config.slippage_bps) / Decimal::from(10_000_u32);
            match working.order.request.side {
                OrderSide::Buy => price * (Decimal::ONE + impact),
                OrderSide::Sell => price * (Decimal::ONE - impact),
            }
        } else {
            price
        };
        let fee = fill_price * quantity * decimal(self.config.fee_bps) / Decimal::from(10_000_u32);
        let at = quote.observed_at_unix_nanos;
        let quantity_value = quantity
            .normalize()
            .to_string()
            .parse::<Quantity>()
            .map_err(|error| error.to_string())?;
        let price_value = fill_price
            .round_dp(18)
            .normalize()
            .to_string()
            .parse::<Price>()
            .map_err(|error| error.to_string())?;
        let fee_value = fee
            .round_dp(18)
            .normalize()
            .to_string()
            .parse::<Money>()
            .map_err(|error| error.to_string())?;
        self.fills.push(SimulationFill {
            fill_id: FillId::new(format!("sim-fill-{}", self.next_fill_id))
                .map_err(|error| error.to_string())?,
            order_id: OrderId::new(order_id.to_string()).map_err(|error| error.to_string())?,
            instrument_id: working.order.request.instrument_id.clone(),
            execution_market_id: working.order.request.market_id.clone(),
            side: working.order.request.side,
            quantity: quantity_value,
            price: price_value,
            fee: fee_value,
            fee_currency: self.config.fee_currency.clone(),
            occurred_at_unix_nanos: at.into(),
        });
        self.next_fill_id += 1;
        let current = self
            .orders
            .get_mut(order_id)
            .ok_or_else(|| format!("simulation order not found: {order_id}"))?;
        current.filled_quantity += quantity;
        current.order.filled_quantity = current
            .filled_quantity
            .normalize()
            .to_string()
            .parse::<Quantity>()
            .map_err(|error| error.to_string())?;
        current.order.remaining_quantity = (current.quantity - current.filled_quantity)
            .normalize()
            .to_string()
            .parse::<Quantity>()
            .map_err(|error| error.to_string())?;
        current.order.updated_at_unix_nanos = at.into();
        current.order.status = if current.filled_quantity >= current.quantity {
            SimulationOrderStatus::Filled
        } else {
            SimulationOrderStatus::PartiallyFilled
        };
        Ok(())
    }
}

/// A historical bar has no bid/ask book. For deterministic backtests we use
/// the close as both sides and carry volume as the available quote quantity.
/// A future execution model can replace this conversion with an explicit
/// intrabar policy without changing the Market replay contract.
fn quote_from_bar(bar: &Bar) -> Quote {
    Quote {
        market_id: bar.market_id.clone(),
        instrument_id: bar.instrument_id.clone(),
        bid_price: Some(bar.close.clone()),
        bid_quantity: bar.volume.clone(),
        ask_price: Some(bar.close.clone()),
        ask_quantity: bar.volume.clone(),
        observed_at_unix_nanos: bar.observed_at_unix_nanos,
        source_id: bar.source_id.clone(),
    }
}

fn default_true() -> bool {
    true
}

fn positive_number(value: &str, field: &str) -> Result<Decimal, String> {
    let number = value
        .trim()
        .parse::<Decimal>()
        .map_err(|error| format!("{field} must be decimal-compatible: {error}"))?;
    if number <= Decimal::ZERO {
        return Err(format!("{field} must be positive"));
    }
    Ok(number)
}

fn decimal(value: Rate) -> Decimal {
    Decimal::try_new(value.mantissa(), u32::from(value.scale()))
        .expect("validated domain rate must fit rust_decimal")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn quote(
        bid: &str,
        ask: &str,
        bid_quantity: &str,
        ask_quantity: &str,
        at: u64,
    ) -> MarketObservation {
        MarketObservation::Quote(Quote {
            market_id: "binance:spot".into(),
            instrument_id: "BTCUSDT".into(),
            bid_price: Some(bid.into()),
            bid_quantity: Some(bid_quantity.into()),
            ask_price: Some(ask.into()),
            ask_quantity: Some(ask_quantity.into()),
            observed_at_unix_nanos: at,
            source_id: "replay".into(),
        })
    }

    fn request(
        order_id: &str,
        side: OrderSide,
        order_type: OrderType,
        quantity: &str,
        limit_price: Option<&str>,
        submitted_at_unix_nanos: u64,
    ) -> SimulationOrderRequest {
        SimulationOrderRequest {
            order_id: OrderId::new(order_id).unwrap(),
            instrument_id: InstrumentId::new("BTCUSDT").unwrap(),
            market_id: None,
            side,
            order_type,
            quantity: quantity.parse().unwrap(),
            limit_price: limit_price.map(|value| value.parse().unwrap()),
            submitted_at_unix_nanos: submitted_at_unix_nanos.into(),
        }
    }

    #[test]
    fn market_buy_fills_at_ask_with_fee_and_slippage() {
        let mut simulator = ExecutionSimulator::new(SimulationConfig {
            fee_bps: "10".parse().unwrap(),
            fee_currency: Some(Currency::new("USDT").unwrap()),
            slippage_bps: "20".parse().unwrap(),
            enforce_quote_quantity: true,
        })
        .unwrap();
        simulator
            .submit(request(
                "order-1",
                OrderSide::Buy,
                OrderType::Market,
                "2",
                None,
                1,
            ))
            .unwrap();
        simulator
            .apply_market_event(quote("99", "100", "10", "2", 2))
            .unwrap();

        let fills = simulator.take_fills();
        assert_eq!(fills.len(), 1);
        assert_eq!(fills[0].fee_currency.as_deref(), Some("USDT"));
        assert_eq!(fills[0].quantity.to_string(), "2");
        assert_eq!(fills[0].price.to_string(), "100.2");
        assert_eq!(fills[0].fee.to_string(), "0.2004");
        assert_eq!(
            simulator.order("order-1").unwrap().status,
            SimulationOrderStatus::Filled
        );
    }

    #[test]
    fn rejects_nonzero_fee_without_payment_currency() {
        let result = ExecutionSimulator::new(SimulationConfig {
            fee_bps: "1".parse().unwrap(),
            fee_currency: None,
            slippage_bps: Rate::ZERO,
            enforce_quote_quantity: true,
        });
        let error = match result {
            Ok(_) => panic!("non-zero simulation fee without currency must fail"),
            Err(error) => error,
        };
        assert!(error.contains("fee_currency is required"));
    }

    #[test]
    fn market_sell_fills_at_bid() {
        let mut simulator = ExecutionSimulator::new(SimulationConfig::default()).unwrap();
        simulator
            .submit(request(
                "sell-order",
                OrderSide::Sell,
                OrderType::Market,
                "1",
                None,
                10,
            ))
            .unwrap();
        simulator
            .apply_market_event(quote("101", "102", "10", "10", 11))
            .unwrap();
        let fills = simulator.take_fills();
        assert_eq!(fills.len(), 1);
        assert_eq!(fills[0].price.to_string(), "101");
        assert_eq!(fills[0].occurred_at_unix_nanos.get(), 11);
    }

    #[test]
    fn historical_bar_fills_market_order_at_close() {
        let mut simulator = ExecutionSimulator::new(SimulationConfig::default()).unwrap();
        simulator
            .submit(request(
                "bar-order",
                OrderSide::Buy,
                OrderType::Market,
                "1",
                None,
                1,
            ))
            .unwrap();
        simulator
            .apply_market_event(MarketObservation::Bar(Bar {
                market_id: "binance:spot".into(),
                instrument_id: "BTCUSDT".into(),
                timeframe: "1m".into(),
                open: "99".into(),
                high: "101".into(),
                low: "98".into(),
                close: "100".into(),
                volume: Some("2".into()),
                observed_at_unix_nanos: 2,
                source_id: "replay".into(),
                derivation: "provider".into(),
            }))
            .unwrap();
        let fills = simulator.take_fills();
        assert_eq!(fills.len(), 1);
        assert_eq!(fills[0].price.to_string(), "100");
    }

    #[test]
    fn limit_order_respects_price_and_quote_quantity() {
        let mut simulator = ExecutionSimulator::new(SimulationConfig::default()).unwrap();
        simulator
            .submit(request(
                "order-2",
                OrderSide::Buy,
                OrderType::Limit,
                "3",
                Some("100"),
                1,
            ))
            .unwrap();
        simulator
            .apply_market_event(quote("99", "101", "10", "1", 2))
            .unwrap();
        assert!(simulator.take_fills().is_empty());
        simulator
            .apply_market_event(quote("99", "100", "10", "1", 3))
            .unwrap();
        let fills = simulator.take_fills();
        assert_eq!(fills[0].quantity.to_string(), "1");
        assert_eq!(
            simulator.order("order-2").unwrap().status,
            SimulationOrderStatus::PartiallyFilled
        );
    }

    #[test]
    fn canceled_order_does_not_fill_on_later_quote() {
        let mut simulator = ExecutionSimulator::new(SimulationConfig::default()).unwrap();
        simulator
            .submit(request(
                "order-3",
                OrderSide::Sell,
                OrderType::Limit,
                "1",
                Some("100"),
                1,
            ))
            .unwrap();
        simulator.cancel("order-3", 2.into()).unwrap();
        simulator
            .apply_market_event(quote("101", "102", "10", "10", 3))
            .unwrap();
        assert!(simulator.take_fills().is_empty());
        assert_eq!(
            simulator.order("order-3").unwrap().status,
            SimulationOrderStatus::Canceled
        );
    }
}
