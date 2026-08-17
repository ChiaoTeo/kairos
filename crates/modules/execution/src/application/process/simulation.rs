//! Process-side driving of the private Execution simulation service.

use super::ExecutionProcess;
use crate::application::ExecutionFillReport;
use crate::application::MarketObservation;
use crate::services::simulation::{SimulationFill, SimulationOrderRequest};

impl<E, Q, S> ExecutionProcess<E, Q, S> {
    pub(super) fn register_simulation_order(
        &mut self,
        order: &crate::domain::ExecutionOrder,
    ) -> Result<(), String> {
        let Some(simulator) = self.simulator.as_mut() else {
            return Ok(());
        };
        if simulator.order(&order.order_id).is_some() {
            return Ok(());
        }
        let submitted_at_unix_nanos = simulator
            .business_time()
            .map(|_| order.submitted_at_unix_nanos)
            .unwrap_or_default();
        simulator
            .submit(SimulationOrderRequest {
                order_id: kairos_primitives::OrderId::new(order.order_id.to_string())
                    .map_err(|error| error.to_string())?,
                instrument_id: kairos_primitives::InstrumentId::new(
                    order.instrument_id.to_string(),
                )
                .map_err(|error| error.to_string())?,
                market_id: order.market_id.clone(),
                side: order.side,
                order_type: order.order_type,
                quantity: kairos_primitives::Quantity::new(
                    order.quantity.mantissa(),
                    order.quantity.scale(),
                )
                .map_err(|error| error.to_string())?,
                limit_price: order
                    .limit_price
                    .map(|price| kairos_primitives::Price::new(price.mantissa(), price.scale()))
                    .transpose()
                    .map_err(|error| error.to_string())?,
                submitted_at_unix_nanos,
            })
            .map(|_| ())
            .map_err(|error| error.to_string())
    }

    pub(super) fn apply_simulated_market(
        &mut self,
        event: MarketObservation,
    ) -> Result<Vec<SimulationFill>, String> {
        let Some(simulator) = self.simulator.as_mut() else {
            return Err("execution simulator is not enabled".into());
        };
        let event_time = match &event {
            MarketObservation::Quote(value) => value.observed_at_unix_nanos,
            MarketObservation::Bar(value) => value.observed_at_unix_nanos,
            MarketObservation::TradeBar(value) => value.bar.observed_at_unix_nanos,
            MarketObservation::QuoteBar(value) => value.bar.observed_at_unix_nanos,
        };
        if event_time != 0 {
            simulator.set_business_time(event_time.into());
        }
        simulator.apply_market_event(event)?;
        let fills = simulator.take_fills();
        for fill in &fills {
            self.application
                .record_fill(ExecutionFillReport {
                    fill_id: fill.fill_id.clone(),
                    order_id: fill.order_id.clone(),
                    quantity: fill.quantity,
                    price: fill.price,
                    fee: fill.fee,
                    fee_currency: fill.fee_currency.clone(),
                    occurred_at_unix_nanos: Some(fill.occurred_at_unix_nanos),
                    execution_market_id: fill.execution_market_id.clone(),
                })
                .map_err(|error| error.to_string())?;
        }
        Ok(fills)
    }
}
