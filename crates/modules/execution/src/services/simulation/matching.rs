use super::model::WorkingOrder;
use super::*;

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
