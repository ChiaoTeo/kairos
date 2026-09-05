//! Pure order-admission rules and exact decimal conversions.

use kairos_primitives::decimal::{Money, Price, Quantity, SignedQuantity};
use kairos_primitives::reference::{InstrumentId, MarketId};
use kairos_primitives::time::{DurationNanos, UnixNanos};
use rust_decimal::Decimal;

use crate::domain::{
    CommitmentBasis, CommitmentResource, ExecuteStrategyIntent, OrderCommitment, OrderError,
    OrderSide, SubmitOrder,
};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AdmissionRule {
    MarketNotTradable,
    QuantityBelowMinimum,
    QuantityTickViolation,
    PriceTickViolation,
    NotionalBelowMinimum,
    QuoteUnavailable,
    QuoteWithoutExecutableSide,
    LimitPriceDeviation,
    PairRequiresTwoLegs,
    PairQuoteNotPositive,
    PairLimitInvalid,
    PairSlippageExceeded,
    PairMissingBuyLeg,
    PairMissingSellLeg,
    PairEdgeBelowMinimum,
    QuoteMissingBid,
    QuoteMissingAsk,
    QuoteInstrumentMismatch,
    QuoteRequiresPostOnly,
    QuoteBidNotLimit,
    QuoteAskNotLimit,
    QuotePriceInvalid,
    QuoteSpreadInvalid,
    QuoteObservationInFuture,
    QuoteStale,
}

impl std::fmt::Display for AdmissionRule {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(match self {
            Self::MarketNotTradable => "market is not tradable",
            Self::QuantityBelowMinimum => "quantity is below the market minimum",
            Self::QuantityTickViolation => "quantity does not align with the market tick",
            Self::PriceTickViolation => "price does not align with the market tick",
            Self::NotionalBelowMinimum => "notional is below the market minimum",
            Self::QuoteUnavailable => "an executable quote is unavailable",
            Self::QuoteWithoutExecutableSide => "the quote has no executable side",
            Self::LimitPriceDeviation => "the limit price exceeds the allowed deviation",
            Self::PairRequiresTwoLegs => "pair execution requires exactly two legs",
            Self::PairQuoteNotPositive => "pair quote prices must be positive",
            Self::PairLimitInvalid => "pair limit price is invalid",
            Self::PairSlippageExceeded => "pair slippage exceeds the configured maximum",
            Self::PairMissingBuyLeg => "pair execution has no buy leg",
            Self::PairMissingSellLeg => "pair execution has no sell leg",
            Self::PairEdgeBelowMinimum => "pair edge is below the configured minimum",
            Self::QuoteMissingBid => "quote is missing a bid",
            Self::QuoteMissingAsk => "quote is missing an ask",
            Self::QuoteInstrumentMismatch => "quote instrument does not match the request",
            Self::QuoteRequiresPostOnly => "quote provisioning requires post-only orders",
            Self::QuoteBidNotLimit => "quote bid must be a limit order",
            Self::QuoteAskNotLimit => "quote ask must be a limit order",
            Self::QuotePriceInvalid => "quote price is invalid",
            Self::QuoteSpreadInvalid => "quote spread is invalid",
            Self::QuoteObservationInFuture => "quote observation is in the future",
            Self::QuoteStale => "quote is stale",
        })
    }
}

impl AdmissionRule {
    pub const fn code(self) -> &'static str {
        match self {
            Self::MarketNotTradable => "execution.admission.market_not_tradable",
            Self::QuantityBelowMinimum => "execution.admission.quantity_below_minimum",
            Self::QuantityTickViolation => "execution.admission.quantity_tick",
            Self::PriceTickViolation => "execution.admission.price_tick",
            Self::NotionalBelowMinimum => "execution.admission.notional_below_minimum",
            Self::QuoteUnavailable => "execution.admission.quote_unavailable",
            Self::QuoteWithoutExecutableSide => "execution.admission.quote_without_executable_side",
            Self::LimitPriceDeviation => "execution.admission.limit_price_deviation",
            Self::PairRequiresTwoLegs => "execution.admission.pair_requires_two_legs",
            Self::PairQuoteNotPositive => "execution.admission.pair_quote_not_positive",
            Self::PairLimitInvalid => "execution.admission.pair_limit_invalid",
            Self::PairSlippageExceeded => "execution.admission.pair_slippage_exceeded",
            Self::PairMissingBuyLeg => "execution.admission.pair_missing_buy_leg",
            Self::PairMissingSellLeg => "execution.admission.pair_missing_sell_leg",
            Self::PairEdgeBelowMinimum => "execution.admission.pair_edge_below_minimum",
            Self::QuoteMissingBid => "execution.admission.quote_missing_bid",
            Self::QuoteMissingAsk => "execution.admission.quote_missing_ask",
            Self::QuoteInstrumentMismatch => "execution.admission.quote_instrument_mismatch",
            Self::QuoteRequiresPostOnly => "execution.admission.quote_requires_post_only",
            Self::QuoteBidNotLimit => "execution.admission.quote_bid_not_limit",
            Self::QuoteAskNotLimit => "execution.admission.quote_ask_not_limit",
            Self::QuotePriceInvalid => "execution.admission.quote_price_invalid",
            Self::QuoteSpreadInvalid => "execution.admission.quote_spread_invalid",
            Self::QuoteObservationInFuture => "execution.admission.quote_observation_in_future",
            Self::QuoteStale => "execution.admission.quote_stale",
        }
    }
}

#[derive(Clone, Debug, thiserror::Error)]
pub enum AdmissionError {
    #[error("execution admission rule failed: {rule}")]
    Rule { rule: AdmissionRule },
    #[error("execution admission decimal conversion failed for {value_kind}")]
    DecimalRepresentation { value_kind: &'static str },
    #[error("execution admission value is outside the supported range: {value_kind}")]
    DecimalRange { value_kind: &'static str },
    #[error("execution admission arithmetic overflow during {operation}")]
    Overflow { operation: &'static str },
    #[error("insufficient available balance for {asset}")]
    InsufficientCapacity { asset: String },
    #[error("invalid execution admission {field}: {source}")]
    InvalidSemantic {
        field: &'static str,
        #[source]
        source: kairos_primitives::DomainTypeError,
    },
    #[error(transparent)]
    Order(#[from] OrderError),
    #[error("execution admission dependency failed: {detail}")]
    Dependency { detail: String },
    #[error("execution admission validation failed: {rule}")]
    Validation { rule: &'static str },
    #[error("execution admission validation failed: {rule} ({subject})")]
    ValidationContext { rule: &'static str, subject: String },
}

impl AdmissionError {
    pub const fn code(&self) -> &'static str {
        match self {
            Self::Rule { rule } => rule.code(),
            Self::DecimalRepresentation { .. } => "execution.admission.decimal_representation",
            Self::DecimalRange { .. } => "execution.admission.decimal_range",
            Self::Overflow { .. } => "execution.admission.overflow",
            Self::InsufficientCapacity { .. } => "execution.admission.insufficient_capacity",
            Self::InvalidSemantic { .. } => "execution.admission.invalid_semantic",
            Self::Order(error) => error.code(),
            Self::Dependency { .. } => "execution.admission.dependency",
            Self::Validation { .. } => "execution.admission.validation",
            Self::ValidationContext { .. } => "execution.admission.validation",
        }
    }

    const fn rule(rule: AdmissionRule) -> Self {
        Self::Rule { rule }
    }

    pub const fn retryable(&self) -> bool {
        matches!(self, Self::Dependency { .. })
    }

    pub(crate) fn validation_context(rule: &'static str, subject: impl Into<String>) -> Self {
        Self::ValidationContext {
            rule,
            subject: subject.into(),
        }
    }
}

impl From<String> for AdmissionError {
    fn from(detail: String) -> Self {
        Self::Dependency { detail }
    }
}

impl From<&'static str> for AdmissionError {
    fn from(rule: &'static str) -> Self {
        Self::Validation { rule }
    }
}

#[derive(Clone, Debug)]
pub(crate) struct ExecutionMarketRules {
    pub(crate) tradable: bool,
    pub(crate) minimum_quantity: Option<Quantity>,
    pub(crate) quantity_tick: Option<Quantity>,
    pub(crate) price_tick: Option<Price>,
    pub(crate) minimum_notional: Option<Money>,
}

/// Composition-owned Market observations are reduced to this typed business
/// fact before Execution planning or admission evaluates them.
#[derive(Clone, Debug)]
pub(crate) struct PlanningQuote {
    pub(crate) market_id: MarketId,
    pub(crate) instrument_id: InstrumentId,
    pub(crate) bid_price: Option<Price>,
    pub(crate) ask_price: Option<Price>,
    pub(crate) observed_at_unix_nanos: UnixNanos,
}

const MAX_PRICE_DEVIATION_BPS: i64 = 500;

pub(crate) fn validate_reference_rules(
    market: &ExecutionMarketRules,
    request: &SubmitOrder,
) -> Result<(), AdmissionError> {
    if !market.tradable {
        return Err(AdmissionError::rule(AdmissionRule::MarketNotTradable));
    }
    if let Some(minimum) = market.minimum_quantity {
        if request.quantity < minimum {
            return Err(AdmissionError::rule(AdmissionRule::QuantityBelowMinimum));
        }
    }
    if let Some(tick) = market.quantity_tick {
        if !request.quantity.is_multiple_of(tick).map_err(|source| {
            AdmissionError::InvalidSemantic {
                field: "quantity_tick",
                source,
            }
        })? {
            return Err(AdmissionError::rule(AdmissionRule::QuantityTickViolation));
        }
    }
    if let Some(price_value) = request.limit_price {
        if let Some(tick) = market.price_tick {
            if !price_value.is_multiple_of(tick).map_err(|source| {
                AdmissionError::InvalidSemantic {
                    field: "price_tick",
                    source,
                }
            })? {
                return Err(AdmissionError::rule(AdmissionRule::PriceTickViolation));
            }
        }
        if let Some(minimum) = market.minimum_notional {
            if price_value
                .checked_mul(request.quantity)
                .map_err(|source| AdmissionError::InvalidSemantic {
                    field: "order_notional",
                    source,
                })?
                < minimum
            {
                return Err(AdmissionError::rule(AdmissionRule::NotionalBelowMinimum));
            }
        }
    }
    Ok(())
}

pub(crate) fn validate_market_price(
    quotes: &[PlanningQuote],
    intent: &ExecuteStrategyIntent,
    limit: Price,
) -> Result<(), AdmissionError> {
    let quote = quotes
        .iter()
        .find(|quote| quote.instrument_id == intent.instrument_id)
        .ok_or(AdmissionError::rule(AdmissionRule::QuoteUnavailable))?;
    let reference = quote
        .ask_price
        .or(quote.bid_price)
        .ok_or(AdmissionError::rule(
            AdmissionRule::QuoteWithoutExecutableSide,
        ))?;
    let limit = decimal_price(limit)?;
    let reference = decimal_price(reference)?;
    if reference <= Decimal::ZERO
        || ((limit - reference).abs() / reference) * Decimal::from(10_000_u32)
            > Decimal::from(MAX_PRICE_DEVIATION_BPS)
    {
        return Err(AdmissionError::rule(AdmissionRule::LimitPriceDeviation));
    }
    Ok(())
}

pub(crate) fn validate_pair_constraints(
    intent: &ExecuteStrategyIntent,
    orders: &[SubmitOrder],
    quotes: &[PlanningQuote],
) -> Result<(), AdmissionError> {
    if orders.len() < 2 {
        return Err(AdmissionError::rule(AdmissionRule::PairRequiresTwoLegs));
    }
    let mut buy_price = None;
    let mut sell_price = None;
    for order in orders {
        let quote = quotes
            .iter()
            .find(|quote| {
                quote.instrument_id == order.instrument_id
                    && order
                        .market_id
                        .as_ref()
                        .is_none_or(|market_id| &quote.market_id == market_id)
            })
            .ok_or(AdmissionError::rule(AdmissionRule::QuoteUnavailable))?;
        let executable = match order.side {
            OrderSide::Buy => quote.ask_price,
            OrderSide::Sell => quote.bid_price,
        }
        .ok_or(AdmissionError::rule(
            AdmissionRule::QuoteWithoutExecutableSide,
        ))?;
        let executable = decimal_price(executable)?;
        if executable <= Decimal::ZERO {
            return Err(AdmissionError::rule(AdmissionRule::PairQuoteNotPositive));
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
                .map_err(|_| AdmissionError::rule(AdmissionRule::PairLimitInvalid))?;
            let slippage_bps = match order.side {
                OrderSide::Buy => (limit - executable) / executable * Decimal::from(10_000_u32),
                OrderSide::Sell => (executable - limit) / executable * Decimal::from(10_000_u32),
            };
            if slippage_bps > Decimal::from(max_slippage) {
                return Err(AdmissionError::rule(AdmissionRule::PairSlippageExceeded));
            }
        }
    }
    if let Some(min_edge) = intent.min_edge_bps {
        let buy = buy_price.ok_or(AdmissionError::rule(AdmissionRule::PairMissingBuyLeg))?;
        let sell = sell_price.ok_or(AdmissionError::rule(AdmissionRule::PairMissingSellLeg))?;
        let gross_edge_bps = (sell - buy) / buy * Decimal::from(10_000_u32);
        let net_edge_bps =
            gross_edge_bps - Decimal::from(intent.estimated_fee_bps.unwrap_or_default());
        if net_edge_bps < Decimal::from(min_edge) {
            return Err(AdmissionError::rule(AdmissionRule::PairEdgeBelowMinimum));
        }
    }
    Ok(())
}

pub(crate) fn validate_quote_provisioning(orders: &[SubmitOrder]) -> Result<(), AdmissionError> {
    let bid = orders
        .iter()
        .find(|order| order.side == OrderSide::Buy)
        .ok_or(AdmissionError::rule(AdmissionRule::QuoteMissingBid))?;
    let ask = orders
        .iter()
        .find(|order| order.side == OrderSide::Sell)
        .ok_or(AdmissionError::rule(AdmissionRule::QuoteMissingAsk))?;
    if bid.instrument_id != ask.instrument_id || bid.market_id != ask.market_id {
        return Err(AdmissionError::rule(AdmissionRule::QuoteInstrumentMismatch));
    }
    if bid.options.post_only != Some(true) || ask.options.post_only != Some(true) {
        return Err(AdmissionError::rule(AdmissionRule::QuoteRequiresPostOnly));
    }
    let bid_price = bid
        .limit_price
        .map(|price| (price.mantissa(), price.scale()))
        .ok_or(AdmissionError::rule(AdmissionRule::QuoteBidNotLimit))?;
    let ask_price = ask
        .limit_price
        .map(|price| (price.mantissa(), price.scale()))
        .ok_or(AdmissionError::rule(AdmissionRule::QuoteAskNotLimit))?;
    let bid_value = Decimal::try_new(bid_price.0, u32::from(bid_price.1))
        .map_err(|_| AdmissionError::rule(AdmissionRule::QuotePriceInvalid))?;
    let ask_value = Decimal::try_new(ask_price.0, u32::from(ask_price.1))
        .map_err(|_| AdmissionError::rule(AdmissionRule::QuotePriceInvalid))?;
    if bid_value <= Decimal::ZERO || ask_value <= bid_value {
        return Err(AdmissionError::rule(AdmissionRule::QuoteSpreadInvalid));
    }
    Ok(())
}

pub(crate) fn validate_quote_freshness(
    orders: &[SubmitOrder],
    quotes: &[PlanningQuote],
    business_time_unix_nanos: u64,
    authoritative_max_age: Option<DurationNanos>,
) -> Result<(), AdmissionError> {
    for order in orders {
        let Some(max_age) = authoritative_max_age.or_else(|| {
            order
                .options
                .maker
                .as_ref()
                .and_then(|policy| policy.max_quote_age)
        }) else {
            continue;
        };
        let quote = quotes
            .iter()
            .find(|quote| {
                quote.instrument_id == order.instrument_id
                    && order
                        .market_id
                        .as_ref()
                        .is_none_or(|market_id| &quote.market_id == market_id)
            })
            .ok_or(AdmissionError::rule(AdmissionRule::QuoteUnavailable))?;
        if quote.observed_at_unix_nanos.get() > business_time_unix_nanos {
            return Err(AdmissionError::rule(
                AdmissionRule::QuoteObservationInFuture,
            ));
        }
        let age = business_time_unix_nanos.saturating_sub(quote.observed_at_unix_nanos.get());
        if age > max_age.get() {
            return Err(AdmissionError::rule(AdmissionRule::QuoteStale));
        }
    }
    Ok(())
}

pub(crate) fn decimal_quantity(value: Quantity) -> Result<Decimal, AdmissionError> {
    Decimal::try_new(value.mantissa(), u32::from(value.scale())).map_err(|_| {
        AdmissionError::DecimalRepresentation {
            value_kind: "quantity",
        }
    })
}

pub(crate) fn decimal_signed_quantity(value: SignedQuantity) -> Result<Decimal, AdmissionError> {
    Decimal::try_new(value.mantissa(), u32::from(value.scale())).map_err(|_| {
        AdmissionError::DecimalRepresentation {
            value_kind: "signed quantity",
        }
    })
}

pub(crate) fn quantity_from_decimal(value: Decimal) -> Result<Quantity, AdmissionError> {
    let value = value.normalize();
    if value < Decimal::ZERO
        || value.scale() > u32::from(kairos_primitives::decimal::MAX_DECIMAL_SCALE)
    {
        return Err(AdmissionError::DecimalRange {
            value_kind: "quantity",
        });
    }
    Quantity::new(
        i64::try_from(value.mantissa()).map_err(|_| AdmissionError::DecimalRange {
            value_kind: "quantity mantissa",
        })?,
        value.scale() as u8,
    )
    .map_err(|source| AdmissionError::InvalidSemantic {
        field: "quantity",
        source,
    })
}

pub(crate) fn decimal_price(value: Price) -> Result<Decimal, AdmissionError> {
    Decimal::try_new(value.mantissa(), u32::from(value.scale())).map_err(|_| {
        AdmissionError::DecimalRepresentation {
            value_kind: "price",
        }
    })
}

pub(crate) fn decimal_money(value: Money) -> Result<Decimal, AdmissionError> {
    Decimal::try_new(value.mantissa(), u32::from(value.scale())).map_err(|_| {
        AdmissionError::DecimalRepresentation {
            value_kind: "money",
        }
    })
}

pub(crate) fn money_from_decimal(value: Decimal) -> Result<Money, AdmissionError> {
    let value = value.normalize();
    if value <= Decimal::ZERO
        || value.scale() > u32::from(kairos_primitives::decimal::MAX_DECIMAL_SCALE)
    {
        return Err(AdmissionError::DecimalRange {
            value_kind: "commitment amount",
        });
    }
    Money::new(
        i64::try_from(value.mantissa()).map_err(|_| AdmissionError::DecimalRange {
            value_kind: "commitment amount mantissa",
        })?,
        value.scale() as u8,
    )
    .map_err(|source| AdmissionError::InvalidSemantic {
        field: "commitment_amount",
        source,
    })
}

pub(crate) fn ensure_available_capacity(
    available: Decimal,
    committed: Decimal,
    needed: Decimal,
    asset: &str,
) -> Result<(), AdmissionError> {
    let remaining = available
        .checked_sub(committed)
        .ok_or(AdmissionError::Overflow {
            operation: "available balance subtraction",
        })?;
    if remaining < needed {
        return Err(AdmissionError::InsufficientCapacity {
            asset: asset.to_owned(),
        });
    }
    Ok(())
}

pub(crate) fn simulation_commitment(
    request: &SubmitOrder,
    now: u64,
) -> Result<OrderCommitment, AdmissionError> {
    let mut commitment = OrderCommitment::new(
        request.order_id.clone(),
        request.account_id.clone(),
        request.segment_key.clone(),
        request.instrument_id.clone(),
        request.side,
        CommitmentResource::Instrument(request.instrument_id.clone()),
        Money::new(request.quantity.mantissa(), request.quantity.scale()).map_err(|source| {
            AdmissionError::InvalidSemantic {
                field: "commitment_amount",
                source,
            }
        })?,
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
        .map(kairos_primitives::reference::Currency::new)
        .transpose()
        .map_err(|source| AdmissionError::InvalidSemantic {
            field: "settlement_asset",
            source,
        })?;
    Ok(commitment)
}

#[cfg(test)]
mod tests {
    use rust_decimal::Decimal;

    use super::{AdmissionError, AdmissionRule, ensure_available_capacity};

    #[test]
    fn active_commitments_reduce_effective_available_capacity() {
        assert!(
            ensure_available_capacity(Decimal::ONE, Decimal::ONE, Decimal::ONE, "USD").is_err()
        );
        assert!(
            ensure_available_capacity(Decimal::from(2), Decimal::ONE, Decimal::ONE, "USD").is_ok()
        );
    }

    #[test]
    fn rule_codes_are_stable_and_business_rejections_are_not_retryable() {
        let error = AdmissionError::Rule {
            rule: AdmissionRule::QuoteStale,
        };
        assert_eq!(error.code(), "execution.admission.quote_stale");
        assert!(!error.retryable());

        let dependency = AdmissionError::Dependency {
            detail: "transport unavailable".into(),
        };
        assert_eq!(dependency.code(), "execution.admission.dependency");
        assert!(dependency.retryable());
    }
}
