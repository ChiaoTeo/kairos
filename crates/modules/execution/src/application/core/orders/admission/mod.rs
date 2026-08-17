//! Pure order-admission rules and exact decimal conversions.

use kairos_primitives::{Money, Price, Quantity, SignedQuantity, UnixNanos};
use kairos_reference_contract::ReferenceMarket;
#[cfg(test)]
use kairos_risk_contract::Amount as RiskAmount;
use rust_decimal::Decimal;

use crate::application::{ExecuteStrategyIntent, SubmitOrder};
use crate::domain::{CommitmentBasis, CommitmentResource, OrderCommitment, OrderSide};

/// Composition-owned Market observations are reduced to this typed business
/// fact before Execution planning or admission evaluates them.
#[derive(Clone, Debug)]
pub(crate) struct PlanningQuote {
    pub(crate) market_id: String,
    pub(crate) instrument_id: String,
    pub(crate) bid_price: Option<String>,
    pub(crate) ask_price: Option<String>,
    pub(crate) observed_at_unix_nanos: u64,
}

const MAX_PRICE_DEVIATION_BPS: i64 = 500;

pub(crate) fn validate_reference_rules(
    market: &ReferenceMarket,
    request: &SubmitOrder,
) -> Result<(), String> {
    let status = market.status.as_str();
    if !matches!(
        status.to_ascii_lowercase().as_str(),
        "active" | "listed" | "trading"
    ) {
        return Err("instrument or market is not tradable".into());
    }
    if let Some(minimum) = market
        .minimum_quantity
        .as_deref()
        .map(str::parse::<Quantity>)
        .transpose()
        .map_err(|error| error.to_string())?
    {
        if request.quantity < minimum {
            return Err("order quantity is below the market minimum".into());
        }
    }
    if let Some(tick) = market
        .quantity_tick
        .as_deref()
        .map(str::parse::<Quantity>)
        .transpose()
        .map_err(|error| error.to_string())?
    {
        if !request
            .quantity
            .is_multiple_of(tick)
            .map_err(|error| error.to_string())?
        {
            return Err("order quantity violates lot size".into());
        }
    }
    if let Some(price_value) = request.limit_price {
        if let Some(tick) = market
            .price_tick
            .as_deref()
            .map(str::parse::<Price>)
            .transpose()
            .map_err(|error| error.to_string())?
        {
            if !price_value
                .is_multiple_of(tick)
                .map_err(|error| error.to_string())?
            {
                return Err("order price violates tick size".into());
            }
        }
        if let Some(minimum) = market
            .minimum_notional
            .as_deref()
            .map(str::parse::<Money>)
            .transpose()
            .map_err(|error| error.to_string())?
        {
            if price_value
                .checked_mul(request.quantity)
                .map_err(|error| error.to_string())?
                < minimum
            {
                return Err("order notional is below the market minimum".into());
            }
        }
    }
    Ok(())
}

pub(crate) fn validate_market_price(
    quotes: &[PlanningQuote],
    intent: &ExecuteStrategyIntent,
    limit: Price,
) -> Result<(), String> {
    let quote = quotes
        .iter()
        .find(|quote| {
            quote
                .instrument_id
                .eq_ignore_ascii_case(intent.instrument_id.as_str())
        })
        .ok_or_else(|| "market quote is unavailable".to_string())?;
    let reference = quote
        .ask_price
        .clone()
        .or_else(|| quote.bid_price.clone())
        .ok_or_else(|| "market quote has no executable side".to_string())?;
    let limit = decimal_price(limit)?;
    let reference = Decimal::from_str_exact(&reference)
        .map_err(|_| "market quote price is invalid".to_string())?;
    if reference <= Decimal::ZERO
        || ((limit - reference).abs() / reference) * Decimal::from(10_000_u32)
            > Decimal::from(MAX_PRICE_DEVIATION_BPS)
    {
        return Err("limit price deviates too far from the current market quote".into());
    }
    Ok(())
}

pub(crate) fn validate_pair_constraints(
    intent: &ExecuteStrategyIntent,
    orders: &[SubmitOrder],
    quotes: &[PlanningQuote],
) -> Result<(), String> {
    if orders.len() < 2 {
        return Err("pair arbitrage requires at least two executable legs".into());
    }
    let mut buy_price = None;
    let mut sell_price = None;
    for order in orders {
        let quote = quotes
            .iter()
            .find(|quote| {
                quote
                    .instrument_id
                    .eq_ignore_ascii_case(&order.instrument_id)
                    && order
                        .market_id
                        .as_deref()
                        .is_none_or(|market_id| quote.market_id == market_id)
            })
            .ok_or_else(|| format!("market quote is unavailable: {}", order.instrument_id))?;
        let executable = match order.side {
            OrderSide::Buy => quote.ask_price.as_deref(),
            OrderSide::Sell => quote.bid_price.as_deref(),
        }
        .ok_or_else(|| format!("quote has no executable side: {}", order.instrument_id))?
        .parse::<Decimal>()
        .map_err(|_| format!("invalid market quote price: {}", order.instrument_id))?;
        if executable <= Decimal::ZERO {
            return Err(format!(
                "market quote is not positive: {}",
                order.instrument_id
            ));
        }
        match order.side {
            OrderSide::Buy => buy_price = Some(executable),
            OrderSide::Sell => sell_price = Some(executable),
        }
        if let (Some(limit), Some(max_slippage)) = (
            order
                .limit_price
                .map(|price| (price.mantissa(), price.scale())),
            intent.max_slippage_bps,
        ) {
            let limit = Decimal::try_new(limit.0, u32::from(limit.1))
                .map_err(|_| "pair limit price is invalid".to_string())?;
            let slippage_bps = match order.side {
                OrderSide::Buy => (limit - executable) / executable * Decimal::from(10_000_u32),
                OrderSide::Sell => (executable - limit) / executable * Decimal::from(10_000_u32),
            };
            if slippage_bps > Decimal::from(max_slippage) {
                return Err(format!(
                    "pair leg {} exceeds max slippage: {:.2} bps > {} bps",
                    order.instrument_id, slippage_bps, max_slippage
                ));
            }
        }
    }
    if let Some(min_edge) = intent.min_edge_bps {
        let buy = buy_price.ok_or_else(|| "pair arbitrage requires a buy leg".to_string())?;
        let sell = sell_price.ok_or_else(|| "pair arbitrage requires a sell leg".to_string())?;
        let gross_edge_bps = (sell - buy) / buy * Decimal::from(10_000_u32);
        let net_edge_bps =
            gross_edge_bps - Decimal::from(intent.estimated_fee_bps.unwrap_or_default());
        if net_edge_bps < Decimal::from(min_edge) {
            return Err(format!(
                "pair net edge is below minimum: {:.2} bps < {} bps (gross={:.2}, fees={})",
                net_edge_bps,
                min_edge,
                gross_edge_bps,
                intent.estimated_fee_bps.unwrap_or_default()
            ));
        }
    }
    Ok(())
}

pub(crate) fn validate_quote_provisioning(orders: &[SubmitOrder]) -> Result<(), String> {
    let bid = orders
        .iter()
        .find(|order| order.side == OrderSide::Buy)
        .ok_or_else(|| "quote provisioning requires a bid leg".to_string())?;
    let ask = orders
        .iter()
        .find(|order| order.side == OrderSide::Sell)
        .ok_or_else(|| "quote provisioning requires an ask leg".to_string())?;
    if bid.instrument_id != ask.instrument_id || bid.market_id != ask.market_id {
        return Err(
            "quote provisioning bid and ask must target the same instrument and market".into(),
        );
    }
    if bid.options.post_only != Some(true) || ask.options.post_only != Some(true) {
        return Err("quote provisioning requires post_only on both legs".into());
    }
    let bid_price = bid
        .limit_price
        .map(|price| (price.mantissa(), price.scale()))
        .ok_or_else(|| "quote provisioning bid must be a limit order".to_string())?;
    let ask_price = ask
        .limit_price
        .map(|price| (price.mantissa(), price.scale()))
        .ok_or_else(|| "quote provisioning ask must be a limit order".to_string())?;
    let bid_value = Decimal::try_new(bid_price.0, u32::from(bid_price.1))
        .map_err(|_| "quote bid price is invalid".to_string())?;
    let ask_value = Decimal::try_new(ask_price.0, u32::from(ask_price.1))
        .map_err(|_| "quote ask price is invalid".to_string())?;
    if bid_value <= Decimal::ZERO || ask_value <= bid_value {
        return Err("quote provisioning requires a positive bid below ask".into());
    }
    Ok(())
}

pub(crate) fn validate_quote_freshness(
    orders: &[SubmitOrder],
    quotes: &[PlanningQuote],
) -> Result<(), String> {
    let now = now_unix_nanos();
    for order in orders {
        let Some(max_age) = order
            .options
            .maker
            .as_ref()
            .and_then(|policy| policy.max_quote_age)
        else {
            continue;
        };
        let quote = quotes
            .iter()
            .find(|quote| {
                quote
                    .instrument_id
                    .eq_ignore_ascii_case(&order.instrument_id)
                    && order
                        .market_id
                        .as_deref()
                        .is_none_or(|market_id| quote.market_id == market_id)
            })
            .ok_or_else(|| format!("market quote is unavailable: {}", order.instrument_id))?;
        let age = now.saturating_sub(quote.observed_at_unix_nanos);
        if age > max_age.get() {
            return Err(format!(
                "market quote is stale for {}: age={}ms exceeds {}ms",
                order.instrument_id,
                age / 1_000_000,
                max_age.get() / 1_000_000
            ));
        }
    }
    Ok(())
}

pub(crate) fn decimal_quantity(value: Quantity) -> Result<Decimal, String> {
    Decimal::try_new(value.mantissa(), u32::from(value.scale()))
        .map_err(|_| "quantity cannot be represented as a decimal".to_string())
}

pub(crate) fn decimal_signed_quantity(value: SignedQuantity) -> Result<Decimal, String> {
    Decimal::try_new(value.mantissa(), u32::from(value.scale()))
        .map_err(|_| "signed quantity cannot be represented as a decimal".to_string())
}

pub(crate) fn quantity_from_decimal(value: Decimal) -> Result<Quantity, String> {
    let value = value.normalize();
    if value < Decimal::ZERO || value.scale() > u32::from(kairos_primitives::MAX_DECIMAL_SCALE) {
        return Err("quantity is outside the supported decimal range".into());
    }
    Quantity::new(
        i64::try_from(value.mantissa())
            .map_err(|_| "quantity exceeds Decimal64 range".to_string())?,
        value.scale() as u8,
    )
    .map_err(|error| error.to_string())
}

pub(crate) fn decimal_price(value: Price) -> Result<Decimal, String> {
    Decimal::try_new(value.mantissa(), u32::from(value.scale()))
        .map_err(|_| "price cannot be represented as a decimal".to_string())
}

pub(crate) fn decimal_money(value: Money) -> Result<Decimal, String> {
    Decimal::try_new(value.mantissa(), u32::from(value.scale()))
        .map_err(|_| "money cannot be represented as a decimal".to_string())
}

pub(crate) fn money_from_decimal(value: Decimal) -> Result<Money, String> {
    let value = value.normalize();
    if value <= Decimal::ZERO || value.scale() > u32::from(kairos_primitives::MAX_DECIMAL_SCALE) {
        return Err("commitment amount is outside the supported decimal range".into());
    }
    Money::new(
        i64::try_from(value.mantissa())
            .map_err(|_| "commitment amount exceeds Decimal64 range".to_string())?,
        value.scale() as u8,
    )
    .map_err(|error| error.to_string())
}

pub(crate) fn ensure_available_capacity(
    available: Decimal,
    committed: Decimal,
    needed: Decimal,
    asset: &str,
) -> Result<(), String> {
    let remaining = available
        .checked_sub(committed)
        .ok_or_else(|| "available balance subtraction overflow".to_string())?;
    if remaining < needed {
        return Err(format!("insufficient available balance for {asset}"));
    }
    Ok(())
}

pub(crate) fn simulation_commitment(
    request: &SubmitOrder,
    now: u64,
) -> Result<OrderCommitment, String> {
    let mut commitment = OrderCommitment::new(
        request.order_id.clone(),
        request.account_id.clone(),
        request.segment_key.clone(),
        request.instrument_id.clone(),
        request.side,
        CommitmentResource::Instrument(request.instrument_id.clone()),
        Money::new(request.quantity.mantissa(), request.quantity.scale())
            .map_err(|error| error.to_string())?,
        request.quantity,
        CommitmentBasis::SimulationQuantity,
        request
            .submitted_at_unix_nanos
            .unwrap_or_else(|| UnixNanos::new(now)),
    )?;
    commitment.settlement_asset = request
        .options
        .quote_asset
        .as_deref()
        .map(kairos_primitives::Currency::new)
        .transpose()
        .map_err(|error| error.to_string())?;
    Ok(commitment)
}

#[cfg(test)]
pub(crate) fn risk_amount(value: Decimal) -> Result<RiskAmount, String> {
    let value = value.normalize();
    if value.scale() > u32::from(kairos_primitives::MAX_DECIMAL_SCALE) {
        return Err("risk amount exceeds 18 fractional digits".into());
    }
    Ok(RiskAmount {
        mantissa: i64::try_from(value.mantissa())
            .map_err(|_| "risk amount exceeds Decimal64 range".to_string())?,
        scale: value.scale() as u8,
    })
}

pub(crate) fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

#[cfg(test)]
mod tests {
    use super::ensure_available_capacity;
    use rust_decimal::Decimal;

    #[test]
    fn active_commitments_reduce_effective_available_capacity() {
        assert!(
            ensure_available_capacity(Decimal::ONE, Decimal::ONE, Decimal::ONE, "USD").is_err()
        );
        assert!(
            ensure_available_capacity(Decimal::from(2), Decimal::ONE, Decimal::ONE, "USD").is_ok()
        );
    }
}
