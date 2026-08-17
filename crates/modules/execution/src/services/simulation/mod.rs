//! Deterministic order simulation for backtest and paper execution.
//!
//! This module owns simulated order lifecycle and fill generation. It does
//! not mutate Account state; generated fills are handed to Account settlement
//! by the composition/application layer.

use std::collections::BTreeMap;

use crate::application::{Bar, MarketObservation, Quote};
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};

use crate::domain::{OrderSide, OrderType};
use kairos_primitives::{
    Currency, FillId, InstrumentId, MarketId, Money, OrderId, Price, Quantity, Rate, UnixNanos,
};

mod account_settlement;
mod matching;
mod model;

pub use account_settlement::SimulatedAccountSettlement;

pub use model::{
    ExecutionSimulator, SimulationConfig, SimulationFill, SimulationOrder, SimulationOrderRequest,
    SimulationOrderStatus, SimulationResult,
};

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
