//! Intent-planning context and its application-port implementation.

use super::*;

pub(super) struct IntentPlanningContext {
    dependencies: ExecutionDependencyAccess,
    business_time_unix_nanos: Option<u64>,
}

impl std::ops::Deref for IntentPlanningContext {
    type Target = ExecutionDependencyAccess;

    fn deref(&self) -> &Self::Target {
        &self.dependencies
    }
}

impl std::ops::DerefMut for IntentPlanningContext {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.dependencies
    }
}

impl IntentPlanningContext {
    pub(super) fn from_manifest_with_reference_snapshot(
        system: &mut kairos_conflux::ConfluxSystem,
        path: impl AsRef<Path>,
        reference_snapshot: Option<kairos_reference_contract::ExecutionReferenceSnapshot>,
    ) -> Result<Self, String> {
        Ok(Self {
            dependencies: ExecutionDependencyAccess::from_manifest_with_reference_snapshot(
                system,
                path,
                reference_snapshot,
            )?,
            business_time_unix_nanos: None,
        })
    }

    pub(super) fn without_market_snapshot(mut self) -> Self {
        self.dependencies = self.dependencies.without_market_snapshot();
        self
    }

    fn plan_explicit_legs(
        &mut self,
        intent: &ExecuteStrategyIntent,
    ) -> Result<Vec<SubmitOrder>, String> {
        let mut orders = Vec::with_capacity(intent.legs.len());
        for leg in &intent.legs {
            if leg.leg_id.as_str().trim().is_empty()
                || leg.account_id.as_str().trim().is_empty()
                || leg.segment_key.as_str().trim().is_empty()
                || leg.instrument_id.as_str().trim().is_empty()
            {
                return Err("explicit intent leg identity is required".into());
            }
            self.health(leg.account_id.as_str())?;
            let (side, quantity) = if leg.target_position {
                let positions = self
                    .account_dependency_state(leg.account_id.as_str())?
                    .positions;
                let current =
                    find_position(&positions, leg.instrument_id.as_str())?.unwrap_or(Decimal::ZERO);
                let target = decimal_quantity(leg.quantity)?;
                let delta = target
                    .checked_sub(current)
                    .ok_or_else(|| "explicit intent leg quantity overflow".to_string())?;
                if delta == Decimal::ZERO {
                    continue;
                }
                (
                    if delta > Decimal::ZERO {
                        OrderSide::Buy
                    } else {
                        OrderSide::Sell
                    },
                    quantity_from_decimal(delta.abs())?,
                )
            } else {
                if leg.quantity.mantissa() <= 0 {
                    return Err("explicit intent leg quantity must be positive".into());
                }
                (leg.side, leg.quantity)
            };
            orders.push(SubmitOrder {
                order_id: OrderId::new(format!("{}:order:{}", intent.intent_id, leg.leg_id))
                    .map_err(|error| error.to_string())?,
                intent_id: Some(intent.intent_id.clone()),
                strategy_id: Some(
                    StrategyId::new(intent.strategy_id.clone())
                        .map_err(|error| error.to_string())?,
                ),
                account_id: leg.account_id.clone(),
                segment_key: leg.segment_key.clone(),
                instrument_id: leg.instrument_id.clone(),
                market_id: leg.market_id.clone(),
                execution_route_id: leg
                    .execution_route_id
                    .clone()
                    .or_else(|| intent.execution_route_id.clone()),
                side,
                order_type: if leg.limit_price.is_some() {
                    OrderType::Limit
                } else {
                    OrderType::Market
                },
                quantity,
                limit_price: leg.limit_price,
                options: leg.options.clone(),
                submitted_at_unix_nanos: intent.source_event_time_unix_nanos,
            });
        }
        if intent.intent_type == crate::domain::IntentType::PairArbitrage
            && (intent.min_edge_bps.is_some() || intent.max_slippage_bps.is_some())
        {
            let quotes = self.read_market_quotes_for_orders(&orders)?;
            validate_pair_constraints(intent, &orders, &quotes)?;
        }
        if intent.intent_type == crate::domain::IntentType::QuoteProvisioning {
            validate_quote_provisioning(&orders)?;
        }
        if self.market_snapshot.is_some() {
            let quotes = self.read_market_quotes_for_orders(&orders)?;
            let business_time = self
                .business_time_unix_nanos
                .ok_or_else(|| "intent planning requires explicit business time".to_string())?;
            let authoritative_max_age = match &intent.algorithm {
                crate::domain::ExecutionAlgorithmPolicy::PassiveLimit(policy) => {
                    Some(policy.max_quote_age)
                },
                _ => None,
            };
            validate_quote_freshness(&orders, &quotes, business_time, authoritative_max_age)?;
        }
        Ok(orders)
    }
}

impl IntentPlanningContext {
    pub(super) fn advance_time(&mut self, event_time_unix_nanos: u64) -> Result<(), String> {
        if self
            .business_time_unix_nanos
            .is_some_and(|current| event_time_unix_nanos < current)
        {
            return Err("execution business time cannot move backwards".into());
        }
        self.business_time_unix_nanos = Some(event_time_unix_nanos);
        Ok(())
    }

    pub(super) fn dependency_watermarks(&self) -> DependencyWatermarks {
        self.dependency_watermarks.clone()
    }

    pub(super) fn plan_intent(
        &mut self,
        intent: &ExecuteStrategyIntent,
    ) -> Result<Vec<SubmitOrder>, String> {
        self.refresh_account_dependency_states()?;
        self.refresh_watermarks();
        if !intent.legs.is_empty() {
            return self.plan_explicit_legs(intent);
        }
        if self.market_snapshot.is_some() {
            let quotes = self
                .read_market_quote(intent.market_id.as_deref(), intent.instrument_id.as_str())?
                .map(|(quote, _)| vec![quote])
                .unwrap_or_default();
            if quotes.is_empty() {
                return Err("market snapshot has no quotes".into());
            }
            if let Some(limit) = intent.limit_price {
                validate_market_price(&quotes, intent, limit)?;
            }
        }
        let mut orders = Vec::with_capacity(intent.account_ids.len());
        for (index, account_id) in intent.account_ids.iter().enumerate() {
            self.health(account_id.as_str())?;
            let positions = self
                .account_dependency_state(account_id.as_str())?
                .positions;
            let current =
                find_position(&positions, intent.instrument_id.as_str())?.unwrap_or(Decimal::ZERO);
            let target = decimal_quantity(intent.target_quantity)?;
            let delta = target
                .checked_sub(current)
                .ok_or_else(|| "intent quantity overflow".to_string())?;
            if delta == Decimal::ZERO {
                continue;
            }
            orders.push(SubmitOrder {
                order_id: OrderId::new(format!("{}:order:{}", intent.intent_id, index))
                    .map_err(|error| error.to_string())?,
                intent_id: Some(intent.intent_id.clone()),
                strategy_id: Some(
                    StrategyId::new(intent.strategy_id.clone())
                        .map_err(|error| error.to_string())?,
                ),
                account_id: account_id.clone(),
                segment_key: intent.segment_key.clone(),
                instrument_id: intent.instrument_id.clone(),
                market_id: intent.market_id.clone(),
                execution_route_id: intent.execution_route_id.clone(),
                side: if delta > Decimal::ZERO {
                    OrderSide::Buy
                } else {
                    OrderSide::Sell
                },
                order_type: if intent.limit_price.is_some() {
                    OrderType::Limit
                } else {
                    OrderType::Market
                },
                quantity: quantity_from_decimal(delta.abs())?,
                limit_price: intent.limit_price,
                options: intent.order_options.clone(),
                submitted_at_unix_nanos: intent.source_event_time_unix_nanos,
            });
        }
        Ok(orders)
    }

    pub(super) fn latest_quote(
        &mut self,
        instrument_id: &str,
        market_id: Option<&str>,
    ) -> Result<Option<QuoteObservation>, String> {
        self.read_market_quote(market_id, instrument_id)?
            .map(|(quote, _generation)| {
                Ok::<_, String>(QuoteObservation {
                    instrument_id: InstrumentId::new(quote.instrument_id)
                        .map_err(|error| error.to_string())?,
                    market_id: Some(
                        MarketId::new(quote.market_id).map_err(|error| error.to_string())?,
                    ),
                    bid_price: quote
                        .bid_price
                        .map(|value| value.parse::<Price>().map_err(|error| error.to_string()))
                        .transpose()?,
                    ask_price: quote
                        .ask_price
                        .map(|value| value.parse::<Price>().map_err(|error| error.to_string()))
                        .transpose()?,
                    observed_at_unix_nanos: UnixNanos::from(quote.observed_at_unix_nanos),
                })
            })
            .transpose()
    }
}
