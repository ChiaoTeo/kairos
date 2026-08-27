use kairos_primitives::account::{AccountId, PositionSide, SegmentKey};
use kairos_primitives::decimal::DecimalParts;
use kairos_primitives::execution::{OrderId, OrderSide};
use kairos_primitives::integration::RemoteOrderId;
use kairos_primitives::reference::{AssetId, InstrumentId, MarketId};
use kairos_primitives::time::UnixNanos;
use kairos_protocol::decode_event_metadata;
use kairos_protocol::generated::kairos::account::v_2 as fb;
use kairos_protocol::generated::kairos::common::v_2::Decimal64;

use crate::{
    AccountBalance, AccountChange, AccountEarnHolding, AccountEvent, AccountFactProvenance,
    AccountFreshness, AccountObservedOrder, AccountObservedOrderIdentity, AccountPosition,
    AccountPositionIdentity, AccountStatus, AccountStatusChange, AccountValuation, ContractError,
    ContractResult, EarnHoldingState, EarnLiquidity, ObservedOrderStatus,
};

pub fn decode_event(bytes: &[u8]) -> ContractResult<AccountEvent> {
    if !kairos_protocol::flatbuffer::identifier_is_readable(bytes) {
        return Err(ContractError::Invalid(
            "Account v2 event payload is shorter than the FlatBuffers header".into(),
        ));
    }
    macro_rules! decode {
        ($check:ident, $root:ident, $project:ident) => {
            if fb::$check(bytes) {
                return fb::$root(bytes)
                    .map_err(invalid_flatbuffer)
                    .and_then($project);
            }
        };
    }
    decode!(
        balance_upserted_buffer_has_identifier,
        root_as_balance_upserted,
        balance_upserted
    );
    decode!(
        balance_removed_buffer_has_identifier,
        root_as_balance_removed,
        balance_removed
    );
    decode!(
        position_upserted_buffer_has_identifier,
        root_as_position_upserted,
        position_upserted
    );
    decode!(
        position_removed_buffer_has_identifier,
        root_as_position_removed,
        position_removed
    );
    decode!(
        earn_holding_upserted_buffer_has_identifier,
        root_as_earn_holding_upserted,
        earn_holding_upserted
    );
    decode!(
        earn_holding_removed_buffer_has_identifier,
        root_as_earn_holding_removed,
        earn_holding_removed
    );
    decode!(
        valuation_changed_buffer_has_identifier,
        root_as_valuation_changed,
        valuation_changed
    );
    decode!(
        account_status_changed_buffer_has_identifier,
        root_as_account_status_changed,
        status_changed
    );
    decode!(
        observed_order_upserted_buffer_has_identifier,
        root_as_observed_order_upserted,
        observed_order_upserted
    );
    decode!(
        observed_order_removed_buffer_has_identifier,
        root_as_observed_order_removed,
        observed_order_removed
    );
    Err(ContractError::Invalid(
        "unknown Account v2 event identifier".into(),
    ))
}

#[cfg(test)]
mod short_frame_tests {
    use super::decode_event;

    #[test]
    fn short_event_frame_is_rejected_without_panicking() {
        assert!(decode_event(b"invalid").is_err());
    }
}

fn balance_upserted(value: fb::BalanceUpserted<'_>) -> ContractResult<AccountEvent> {
    let raw = value.balance();
    event(
        value.metadata(),
        value.account_id(),
        value.segment_key(),
        value.provenance(),
        AccountChange::BalanceUpserted(AccountBalance {
            asset_id: identity(raw.asset_id(), "balance.asset_id", AssetId::new)?,
            asset_code: optional_text(raw.asset_code(), "balance.asset_code")?,
            total: decimal(raw.total())?,
            available: raw.available().map(decimal).transpose()?,
            locked: raw.locked().map(decimal).transpose()?,
            borrowed: raw.borrowed().map(decimal).transpose()?,
            interest: raw.interest().map(decimal).transpose()?,
        }),
    )
}

fn balance_removed(value: fb::BalanceRemoved<'_>) -> ContractResult<AccountEvent> {
    event(
        value.metadata(),
        value.account_id(),
        value.segment_key(),
        value.provenance(),
        AccountChange::BalanceRemoved {
            asset_id: identity(value.asset_id(), "balance.asset_id", AssetId::new)?,
        },
    )
}

fn position_upserted(value: fb::PositionUpserted<'_>) -> ContractResult<AccountEvent> {
    let raw = value.position();
    event(
        value.metadata(),
        value.account_id(),
        value.segment_key(),
        value.provenance(),
        AccountChange::PositionUpserted(AccountPosition {
            instrument_id: identity(
                raw.instrument_id(),
                "position.instrument_id",
                InstrumentId::new,
            )?,
            market_id: identity(raw.market_id(), "position.market_id", MarketId::new)?,
            position_side: position_side(raw.position_side())?,
            quantity: decimal(raw.quantity())?,
            average_price: raw.average_price().map(decimal).transpose()?,
            mark_price: raw.mark_price().map(decimal).transpose()?,
            unrealized_pnl: raw.unrealized_pnl().map(decimal).transpose()?,
            realized_pnl: raw.realized_pnl().map(decimal).transpose()?,
            observed_at_unix_nanos: optional_nanos(raw.observed_at_unix_nanos()),
        }),
    )
}

fn position_removed(value: fb::PositionRemoved<'_>) -> ContractResult<AccountEvent> {
    event(
        value.metadata(),
        value.account_id(),
        value.segment_key(),
        value.provenance(),
        AccountChange::PositionRemoved(AccountPositionIdentity {
            instrument_id: identity(
                value.instrument_id(),
                "position.instrument_id",
                InstrumentId::new,
            )?,
            market_id: identity(value.market_id(), "position.market_id", MarketId::new)?,
            position_side: position_side(value.position_side())?,
        }),
    )
}

fn earn_holding_upserted(value: fb::EarnHoldingUpserted<'_>) -> ContractResult<AccountEvent> {
    let raw = value.holding();
    event(
        value.metadata(),
        value.account_id(),
        value.segment_key(),
        value.provenance(),
        AccountChange::EarnHoldingUpserted(AccountEarnHolding {
            holding_key: required_text(raw.holding_key(), "earn.holding_key")?,
            participant_position_id: optional_text(
                raw.participant_position_id(),
                "earn.participant_position_id",
            )?,
            product_id: required_text(raw.product_id(), "earn.product_id")?,
            asset: required_text(raw.asset(), "earn.asset")?,
            principal: decimal(raw.principal())?,
            redeemable: raw.redeemable().map(decimal).transpose()?,
            state: earn_state(raw.state())?,
            participant_state: optional_text(raw.participant_state(), "earn.participant_state")?,
            liquidity: earn_liquidity(raw.liquidity())?,
            notice_seconds: optional_u64(raw.notice_seconds()),
            matures_at_unix_nanos: optional_nanos(raw.matures_at_unix_nanos()),
            observed_at_unix_nanos: optional_nanos(raw.observed_at_unix_nanos()),
        }),
    )
}

fn earn_holding_removed(value: fb::EarnHoldingRemoved<'_>) -> ContractResult<AccountEvent> {
    event(
        value.metadata(),
        value.account_id(),
        value.segment_key(),
        value.provenance(),
        AccountChange::EarnHoldingRemoved {
            holding_key: required_text(value.holding_key(), "earn.holding_key")?,
        },
    )
}

fn valuation_changed(value: fb::ValuationChanged<'_>) -> ContractResult<AccountEvent> {
    let raw = value.valuation();
    event(
        value.metadata(),
        value.account_id(),
        value.segment_key(),
        value.provenance(),
        AccountChange::ValuationChanged(AccountValuation {
            valuation_asset_id: raw
                .valuation_asset_id()
                .map(|value| identity(value, "valuation.asset_id", AssetId::new))
                .transpose()?,
            equity: raw.equity().map(decimal).transpose()?,
            initial_equity: raw.initial_equity().map(decimal).transpose()?,
            net_profit: raw.net_profit().map(decimal).transpose()?,
            observed_at_unix_nanos: optional_nanos(raw.observed_at_unix_nanos()),
        }),
    )
}

fn status_changed(value: fb::AccountStatusChanged<'_>) -> ContractResult<AccountEvent> {
    event(
        value.metadata(),
        value.account_id(),
        value.segment_key(),
        value.provenance(),
        AccountChange::StatusChanged(AccountStatusChange {
            status: account_status(value.status())?,
            freshness: freshness(value.freshness())?,
            reason: optional_text(value.reason(), "status.reason")?,
        }),
    )
}

fn observed_order_upserted(value: fb::ObservedOrderUpserted<'_>) -> ContractResult<AccountEvent> {
    let raw = value.order();
    event(
        value.metadata(),
        value.account_id(),
        value.segment_key(),
        value.provenance(),
        AccountChange::ObservedOrderUpserted(AccountObservedOrder {
            observation_id: required_text(raw.observation_id(), "order.observation_id")?,
            source_id: required_text(raw.source_id(), "order.source_id")?,
            execution_order_id: raw
                .execution_order_id()
                .map(|value| identity(value, "order.execution_order_id", OrderId::new))
                .transpose()?,
            remote_order_id: raw
                .remote_order_id()
                .map(|value| identity(value, "order.remote_order_id", RemoteOrderId::new))
                .transpose()?,
            instrument_id: identity(
                raw.instrument_id(),
                "order.instrument_id",
                InstrumentId::new,
            )?,
            market_id: identity(raw.market_id(), "order.market_id", MarketId::new)?,
            side: order_side(raw.side())?,
            quantity: decimal(raw.quantity())?,
            filled_quantity: decimal(raw.filled_quantity())?,
            status: observed_order_status(raw.status())?,
            observed_at_unix_nanos: optional_nanos(raw.observed_at_unix_nanos()),
        }),
    )
}

fn observed_order_removed(value: fb::ObservedOrderRemoved<'_>) -> ContractResult<AccountEvent> {
    event(
        value.metadata(),
        value.account_id(),
        value.segment_key(),
        value.provenance(),
        AccountChange::ObservedOrderRemoved(AccountObservedOrderIdentity {
            observation_id: required_text(value.observation_id(), "order.observation_id")?,
            execution_order_id: value
                .execution_order_id()
                .map(|value| identity(value, "order.execution_order_id", OrderId::new))
                .transpose()?,
            remote_order_id: value
                .remote_order_id()
                .map(|value| identity(value, "order.remote_order_id", RemoteOrderId::new))
                .transpose()?,
        }),
    )
}

fn event(
    metadata: kairos_protocol::generated::kairos::common::v_2::EventMetadata<'_>,
    account_id: &str,
    segment_key: &str,
    provenance: Option<fb::AccountFactProvenance<'_>>,
    change: AccountChange,
) -> ContractResult<AccountEvent> {
    Ok(AccountEvent {
        metadata: decode_event_metadata(metadata)
            .map_err(|error| ContractError::Invalid(error.to_string()))?,
        account_id: identity(account_id, "account_id", AccountId::new)?,
        segment_key: identity(segment_key, "segment_key", SegmentKey::new)?,
        provenance: provenance.map(project_provenance).transpose()?,
        change,
    })
}

fn project_provenance(
    value: fb::AccountFactProvenance<'_>,
) -> ContractResult<AccountFactProvenance> {
    Ok(AccountFactProvenance {
        source_id: required_text(value.source_id(), "provenance.source_id")?,
        provider_event_id: optional_text(
            value.provider_event_id(),
            "provenance.provider_event_id",
        )?,
        provider_sequence: value.provider_sequence(),
        provider_occurred_at_unix_nanos: value
            .provider_occurred_at_unix_nanos()
            .map(UnixNanos::new),
        provider_received_at_unix_nanos: value
            .provider_received_at_unix_nanos()
            .map(UnixNanos::new),
    })
}

fn decimal(value: &Decimal64) -> ContractResult<DecimalParts> {
    DecimalParts::new(value.mantissa(), value.scale())
        .map_err(|error| ContractError::Invalid(error.to_string()))
}

fn position_side(value: fb::PositionSide) -> ContractResult<PositionSide> {
    match value {
        fb::PositionSide::NET => Ok(PositionSide::Net),
        fb::PositionSide::LONG => Ok(PositionSide::Long),
        fb::PositionSide::SHORT => Ok(PositionSide::Short),
        _ => invalid_enum("position_side", value.0),
    }
}

fn earn_state(value: fb::EarnHoldingState) -> ContractResult<EarnHoldingState> {
    match value {
        fb::EarnHoldingState::ACTIVE => Ok(EarnHoldingState::Active),
        fb::EarnHoldingState::REDEEMING => Ok(EarnHoldingState::Redeeming),
        fb::EarnHoldingState::REDEEMED => Ok(EarnHoldingState::Redeemed),
        fb::EarnHoldingState::UNKNOWN => Ok(EarnHoldingState::Unknown),
        _ => invalid_enum("earn_holding_state", value.0),
    }
}

fn earn_liquidity(value: fb::EarnLiquidity) -> ContractResult<EarnLiquidity> {
    match value {
        fb::EarnLiquidity::IMMEDIATE => Ok(EarnLiquidity::Immediate),
        fb::EarnLiquidity::NOTICE => Ok(EarnLiquidity::Notice),
        fb::EarnLiquidity::FIXED_TERM => Ok(EarnLiquidity::FixedTerm),
        fb::EarnLiquidity::UNKNOWN => Ok(EarnLiquidity::Unknown),
        _ => invalid_enum("earn_liquidity", value.0),
    }
}

fn account_status(value: fb::AccountStatus) -> ContractResult<AccountStatus> {
    match value {
        fb::AccountStatus::ACTIVE => Ok(AccountStatus::Active),
        fb::AccountStatus::RESTRICTED => Ok(AccountStatus::Restricted),
        fb::AccountStatus::DISABLED => Ok(AccountStatus::Disabled),
        fb::AccountStatus::CLOSED => Ok(AccountStatus::Closed),
        fb::AccountStatus::ERROR => Ok(AccountStatus::Error),
        fb::AccountStatus::RECONCILING => Ok(AccountStatus::Reconciling),
        fb::AccountStatus::TYPE_MISMATCH => Ok(AccountStatus::TypeMismatch),
        fb::AccountStatus::UNAVAILABLE => Ok(AccountStatus::Unavailable),
        _ => invalid_enum("account_status", value.0),
    }
}

fn freshness(value: fb::FreshnessState) -> ContractResult<AccountFreshness> {
    match value {
        fb::FreshnessState::FRESH => Ok(AccountFreshness::Fresh),
        fb::FreshnessState::STALE => Ok(AccountFreshness::Stale),
        fb::FreshnessState::UNKNOWN => Ok(AccountFreshness::Unknown),
        fb::FreshnessState::RESYNCING => Ok(AccountFreshness::Resyncing),
        fb::FreshnessState::UNAVAILABLE => Ok(AccountFreshness::Unavailable),
        _ => invalid_enum("freshness", value.0),
    }
}

fn order_side(
    value: kairos_protocol::generated::kairos::common::v_2::Side,
) -> ContractResult<OrderSide> {
    match value {
        kairos_protocol::generated::kairos::common::v_2::Side::BUY => Ok(OrderSide::Buy),
        kairos_protocol::generated::kairos::common::v_2::Side::SELL => Ok(OrderSide::Sell),
        _ => invalid_enum("order_side", value.0),
    }
}

fn observed_order_status(value: fb::ObservedOrderStatus) -> ContractResult<ObservedOrderStatus> {
    match value {
        fb::ObservedOrderStatus::OPEN => Ok(ObservedOrderStatus::Open),
        fb::ObservedOrderStatus::PARTIALLY_FILLED => Ok(ObservedOrderStatus::PartiallyFilled),
        fb::ObservedOrderStatus::PENDING_CANCEL => Ok(ObservedOrderStatus::PendingCancel),
        fb::ObservedOrderStatus::CLOSED => Ok(ObservedOrderStatus::Closed),
        fb::ObservedOrderStatus::UNKNOWN => Ok(ObservedOrderStatus::Unknown),
        _ => invalid_enum("observed_order_status", value.0),
    }
}

fn invalid_enum<T>(name: &str, value: u8) -> ContractResult<T> {
    Err(ContractError::Invalid(format!(
        "Account event has invalid {name} value {value}"
    )))
}

fn identity<T>(
    value: &str,
    field: &'static str,
    constructor: impl FnOnce(String) -> Result<T, kairos_primitives::DomainTypeError>,
) -> ContractResult<T> {
    constructor(value.to_owned())
        .map_err(|error| ContractError::Invalid(format!("invalid Account event {field}: {error}")))
}

fn required_text(value: &str, field: &'static str) -> ContractResult<String> {
    if value.is_empty() || value.trim() != value {
        return Err(ContractError::Invalid(format!(
            "Account event {field} is empty or not trimmed"
        )));
    }
    Ok(value.to_owned())
}

fn optional_text(value: Option<&str>, field: &'static str) -> ContractResult<Option<String>> {
    value.map(|value| required_text(value, field)).transpose()
}

fn optional_u64(value: u64) -> Option<u64> {
    (value != 0).then_some(value)
}

fn optional_nanos(value: u64) -> Option<UnixNanos> {
    optional_u64(value).map(UnixNanos::new)
}

fn invalid_flatbuffer(error: flatbuffers::InvalidFlatbuffer) -> ContractError {
    ContractError::Invalid(error.to_string())
}
