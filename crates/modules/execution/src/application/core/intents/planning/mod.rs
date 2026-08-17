//! Pure fallback planning for simulated execution.

use super::super::*;
use kairos_primitives::OrderId;

pub(super) fn plan_simulated_intent(
    intent: &ExecuteStrategyIntent,
) -> Result<Vec<SubmitOrder>, ExecutionError> {
    if intent.target_quantity.is_zero() && intent.legs.is_empty() {
        return Ok(Vec::new());
    }
    if !intent.legs.is_empty() {
        return intent
            .legs
            .iter()
            .map(|leg| {
                Ok(SubmitOrder {
                    order_id: OrderId::new(format!("{}:order:{}", intent.intent_id, leg.leg_id))
                        .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
                    intent_id: Some(intent.intent_id.clone()),
                    strategy_id: Some(typed_strategy_id(intent.strategy_id.clone())),
                    account_id: leg.account_id.clone(),
                    segment_key: leg.segment_key.clone(),
                    instrument_id: leg.instrument_id.clone(),
                    market_id: leg.market_id.clone(),
                    execution_access_id: leg
                        .execution_access_id
                        .clone()
                        .or_else(|| intent.execution_access_id.clone()),
                    side: leg.side,
                    order_type: if leg.limit_price.is_some() {
                        OrderType::Limit
                    } else {
                        OrderType::Market
                    },
                    quantity: leg.quantity,
                    limit_price: leg.limit_price,
                    options: leg.options.clone(),
                    submitted_at_unix_nanos: intent.source_event_time_unix_nanos,
                })
            })
            .collect();
    }
    intent
        .account_ids
        .iter()
        .enumerate()
        .map(|(index, account_id)| {
            Ok(SubmitOrder {
                order_id: OrderId::new(format!("{}:order:{index}", intent.intent_id))
                    .map_err(|error| ExecutionError::Invalid(error.to_string()))?,
                intent_id: Some(intent.intent_id.clone()),
                strategy_id: Some(typed_strategy_id(intent.strategy_id.clone())),
                account_id: account_id.clone(),
                segment_key: intent.segment_key.clone(),
                instrument_id: intent.instrument_id.clone(),
                market_id: intent.market_id.clone(),
                execution_access_id: intent.execution_access_id.clone(),
                side: OrderSide::Buy,
                order_type: if intent.limit_price.is_some() {
                    OrderType::Limit
                } else {
                    OrderType::Market
                },
                quantity: intent.target_quantity,
                limit_price: intent.limit_price,
                options: intent.order_options.clone(),
                submitted_at_unix_nanos: intent.source_event_time_unix_nanos,
            })
        })
        .collect()
}
