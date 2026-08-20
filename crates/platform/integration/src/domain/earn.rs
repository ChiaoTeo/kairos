//! Participant-neutral yield-product requests and external facts.

use kairos_primitives::{Currency, IdempotencyKey, Quantity, Rate, SignedQuantity, UnixNanos};

use crate::domain::account::{ExternalAccountIdentity, ExternalAccountSegment};

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum EarnLiquidity {
    Immediate,
    Notice { notice_seconds: u64 },
    FixedTerm { matures_at_unix_nanos: UnixNanos },
    Unknown,
}

/// The smallest useful cross-participant family. Provider-native product type
/// remains available separately and is authoritative for adapter routing.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum EarnProductFamily {
    Flexible,
    Locked,
    Staking,
    YieldBearingAsset,
    Other(String),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum EarnProductState {
    Available,
    Suspended,
    Closed,
    Unknown(String),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EarnRateKind {
    Apr,
    Apy,
    Unknown,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum EarnRateComponentKind {
    Base,
    Realtime,
    BonusTier,
    Promotional,
    Other(String),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EarnRateComponent {
    pub kind: EarnRateComponentKind,
    pub annual_rate: Rate,
    pub rate_kind: EarnRateKind,
    pub eligible_amount_limit: Option<Quantity>,
    pub valid_from_unix_nanos: Option<UnixNanos>,
    pub valid_until_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EarnRedemptionChannel {
    Immediate,
    Standard,
    Early,
    AtMaturity,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EarnRedemptionOption {
    pub channel: EarnRedemptionChannel,
    pub settlement_delay_seconds: Option<u64>,
    pub remaining_quota: Option<Quantity>,
    pub forfeits_accrued_rewards: Option<bool>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EarnProduct {
    pub product_id: String,
    pub asset: Currency,
    pub family: EarnProductFamily,
    /// Provider product vocabulary is preserved because providers do not share
    /// one reliable Flexible/Locked/Staking taxonomy.
    pub participant_product_type: String,
    /// A product may combine real-time, base, bonus-tier, and promotional
    /// rates. Callers must not sum components unless product terms permit it.
    pub rate_components: Vec<EarnRateComponent>,
    pub minimum_amount: Option<Quantity>,
    pub maximum_amount: Option<Quantity>,
    /// Participant- and principal-specific capacity available at observation
    /// time. It is distinct from the product-wide maximum.
    pub remaining_subscription_quota: Option<Quantity>,
    pub liquidity: EarnLiquidity,
    pub redemption_options: Vec<EarnRedemptionOption>,
    pub state: EarnProductState,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum EarnPositionState {
    Active,
    Redeeming,
    Redeemed,
    Unknown(String),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EarnPosition {
    pub participant_position_id: Option<String>,
    pub product_id: String,
    pub asset: Currency,
    pub family: EarnProductFamily,
    pub principal: Quantity,
    pub accrued_rewards: Vec<EarnAccruedReward>,
    pub redeemable_amount: Option<Quantity>,
    pub subscribed_at_unix_nanos: Option<UnixNanos>,
    pub matures_at_unix_nanos: Option<UnixNanos>,
    pub state: EarnPositionState,
    pub observed_at_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EarnAccruedReward {
    pub asset: Currency,
    pub amount: Quantity,
    pub component: Option<EarnRateComponentKind>,
}

/// A participant-owned reward or correction used for yield attribution and
/// Account reconciliation. The amount is signed because providers may publish
/// corrections and reversals.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EarnReward {
    pub participant_reward_id: Option<String>,
    pub product_id: Option<String>,
    pub asset: Currency,
    pub amount: SignedQuantity,
    pub occurred_at_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EarnRateObservation {
    pub product_id: String,
    pub annual_rate: Rate,
    pub rate_kind: EarnRateKind,
    pub observed_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EarnPage<T> {
    pub items: Vec<T>,
    /// Opaque participant cursor. `None` means that no further page was
    /// reported; callers must not manufacture or interpret this value.
    pub next_cursor: Option<String>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct EarnProductsRequest {
    pub asset: Option<Currency>,
    pub family: Option<EarnProductFamily>,
    pub product_id: Option<String>,
    pub cursor: Option<String>,
    pub limit: Option<u16>,
}

impl EarnProductsRequest {
    pub fn validate(&self) -> Result<(), String> {
        validate_optional_text("earn product id", self.product_id.as_deref())?;
        validate_page(self.cursor.as_deref(), self.limit)
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct EarnPositionsRequest {
    pub asset: Option<Currency>,
    pub family: Option<EarnProductFamily>,
    pub product_id: Option<String>,
    pub cursor: Option<String>,
    pub limit: Option<u16>,
}

impl EarnPositionsRequest {
    pub fn validate(&self) -> Result<(), String> {
        validate_optional_text("earn product id", self.product_id.as_deref())?;
        validate_page(self.cursor.as_deref(), self.limit)
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct EarnHistoryWindow {
    pub start_at_unix_nanos: Option<UnixNanos>,
    pub end_at_unix_nanos: Option<UnixNanos>,
    pub cursor: Option<String>,
    pub limit: Option<u16>,
}

impl EarnHistoryWindow {
    pub fn validate(&self) -> Result<(), String> {
        if self
            .start_at_unix_nanos
            .zip(self.end_at_unix_nanos)
            .is_some_and(|(start, end)| start > end)
        {
            return Err("earn history window is inverted".into());
        }
        validate_page(self.cursor.as_deref(), self.limit)
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct EarnRewardsRequest {
    pub asset: Option<Currency>,
    pub product_id: Option<String>,
    pub window: EarnHistoryWindow,
}

impl EarnRewardsRequest {
    pub fn validate(&self) -> Result<(), String> {
        validate_optional_text("earn product id", self.product_id.as_deref())?;
        self.window.validate()
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct EarnRatesRequest {
    pub product_id: Option<String>,
    pub window: EarnHistoryWindow,
}

impl EarnRatesRequest {
    pub fn validate(&self) -> Result<(), String> {
        validate_optional_text("earn product id", self.product_id.as_deref())?;
        self.window.validate()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EarnSubscriptionPreviewRequest {
    /// Required because quota and eligibility are credential-principal facts.
    pub account: ExternalAccountIdentity,
    pub product_id: String,
    pub amount: Quantity,
}

impl EarnSubscriptionPreviewRequest {
    pub fn validate(&self) -> Result<(), String> {
        if self.account.broker.is_empty() || self.account.broker.trim() != self.account.broker {
            return Err("earn preview account broker is required and must be trimmed".into());
        }
        validate_product_and_amount(&self.product_id, self.amount)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum EarnSubscriptionEligibility {
    Eligible,
    Ineligible { reason: String },
}

/// Point-in-time terms used by Capital/Risk before a subscription command.
/// Providers may change rates or quota after this observation, so this is not
/// a settlement guarantee.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EarnSubscriptionPreview {
    pub product_id: String,
    pub amount: Quantity,
    pub eligibility: EarnSubscriptionEligibility,
    pub rate_components: Vec<EarnRateComponent>,
    pub remaining_subscription_quota: Option<Quantity>,
    pub liquidity: EarnLiquidity,
    pub redemption_options: Vec<EarnRedemptionOption>,
    pub observed_at_unix_nanos: UnixNanos,
}

fn validate_optional_text(name: &str, value: Option<&str>) -> Result<(), String> {
    if value.is_some_and(|value| value.trim().is_empty()) {
        return Err(format!("{name} cannot be empty"));
    }
    Ok(())
}

fn validate_page(cursor: Option<&str>, limit: Option<u16>) -> Result<(), String> {
    if cursor.is_some_and(|cursor| cursor.trim().is_empty()) {
        return Err("earn page cursor cannot be empty".into());
    }
    if limit == Some(0) {
        return Err("earn page limit must be positive".into());
    }
    Ok(())
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EarnSubscribeRequest {
    pub account: crate::ExternalAccountIdentity,
    pub idempotency_key: IdempotencyKey,
    pub product_id: String,
    pub amount: Quantity,
    pub requested_at_unix_nanos: UnixNanos,
}

impl EarnSubscribeRequest {
    pub fn validate(&self) -> Result<(), String> {
        validate_product_and_amount(&self.product_id, self.amount)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EarnRedeemRequest {
    pub account: crate::ExternalAccountIdentity,
    pub idempotency_key: IdempotencyKey,
    pub product_id: String,
    pub amount: EarnRedemptionAmount,
    pub destination: Option<ExternalAccountSegment>,
    pub requested_at_unix_nanos: UnixNanos,
}

impl EarnRedeemRequest {
    pub fn validate(&self) -> Result<(), String> {
        if self.product_id.trim().is_empty() {
            return Err("earn product id is required".into());
        }
        if matches!(self.amount, EarnRedemptionAmount::Exact(amount) if !amount.is_positive()) {
            return Err("earn redemption amount must be positive".into());
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EarnRedemptionAmount {
    All,
    Exact(Quantity),
}

fn validate_product_and_amount(product_id: &str, amount: Quantity) -> Result<(), String> {
    if product_id.trim().is_empty() {
        return Err("earn product id is required".into());
    }
    if !amount.is_positive() {
        return Err("earn subscription amount must be positive".into());
    }
    Ok(())
}

/// Participant acknowledgement of an Earn command, not proof of settlement.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EarnSubmission {
    pub participant_action_id: Option<String>,
    pub acknowledged_at_unix_nanos: Option<UnixNanos>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EarnActionQuery {
    pub account: crate::ExternalAccountIdentity,
    pub idempotency_key: IdempotencyKey,
    pub participant_action_id: Option<String>,
    pub action: EarnActionKind,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EarnActionKind {
    Subscribe,
    Redeem,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EarnActionState {
    Pending,
    Succeeded,
    Failed,
    Unknown,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EarnActionStatus {
    pub idempotency_key: IdempotencyKey,
    pub participant_action_id: Option<String>,
    pub action: EarnActionKind,
    pub state: EarnActionState,
    pub participant_state: Option<String>,
    pub updated_at_unix_nanos: Option<UnixNanos>,
    pub failure_reason: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_zero_subscription_without_encoding_acceptance_twice() {
        let request = EarnSubscribeRequest {
            account: crate::ExternalAccountIdentity::new("binance", "account-1").unwrap(),
            idempotency_key: IdempotencyKey::new("capital-plan:1:earn:1").unwrap(),
            product_id: "flexible-usdt".into(),
            amount: Quantity::ZERO,
            requested_at_unix_nanos: UnixNanos::new(1),
        };
        assert_eq!(
            request.validate(),
            Err("earn subscription amount must be positive".into())
        );
    }

    #[test]
    fn history_queries_are_bounded_and_cursor_is_opaque_non_empty_text() {
        assert_eq!(
            EarnHistoryWindow {
                start_at_unix_nanos: Some(UnixNanos::new(2)),
                end_at_unix_nanos: Some(UnixNanos::new(1)),
                ..Default::default()
            }
            .validate(),
            Err("earn history window is inverted".into())
        );
        assert_eq!(
            EarnHistoryWindow {
                cursor: Some(" ".into()),
                ..Default::default()
            }
            .validate(),
            Err("earn page cursor cannot be empty".into())
        );
    }
}
