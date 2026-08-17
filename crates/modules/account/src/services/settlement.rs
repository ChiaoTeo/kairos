use crate::application::AccountSegmentView;
use crate::domain::{
    AccountDomainError, AccountFill, AccountSnapshot, AssetId, Balance, Money, OrderSide, Position,
    SignedQuantity, SnapshotKind,
};

/// Builds authoritative account deltas for deterministic paper/simulated fills.
/// Live fills never use this calculation; live balances and positions come
/// from provider observations.
pub(crate) fn settle_paper_fill(
    account: &AccountSegmentView,
    fill: &AccountFill,
) -> Result<AccountSnapshot, AccountDomainError> {
    if account.segment_key != fill.segment_key {
        return Err(AccountDomainError::SegmentMismatch {
            expected: account.segment_key.to_string(),
            observed: fill.segment_key.to_string(),
        });
    }

    let mut position = account
        .positions
        .iter()
        .find(|position| position.instrument_id == fill.instrument_id)
        .cloned()
        .unwrap_or_else(|| Position {
            instrument_id: fill.instrument_id.clone(),
            market_id: None,
            position_side: kairos_primitives::PositionSide::Net,
            quantity: SignedQuantity::ZERO,
            average_price: None,
            mark_price: None,
            unrealized_pnl: None,
            realized_pnl: None,
            updated_at_unix_nanos: kairos_primitives::UnixNanos::new(0),
        });
    let previous_quantity = position.quantity;
    let previous_average = position
        .average_price
        .unwrap_or_else(|| kairos_primitives::Price::new(1, 0).expect("positive fallback price"));
    let fill_quantity = SignedQuantity::new(fill.quantity.mantissa(), fill.quantity.scale())?;
    let (next_quantity, next_average, realized_pnl) = match fill.side {
        OrderSide::Buy => {
            let next_quantity = previous_quantity.checked_add(fill_quantity)?;
            let next_average =
                if previous_quantity.is_positive() && position.average_price.is_some() {
                    previous_quantity
                        .checked_mul(previous_average)?
                        .checked_add(fill.price.checked_mul(fill.quantity)?)?
                        .checked_div(next_quantity)?
                } else {
                    fill.price
                };
            (next_quantity, next_average, Money::ZERO)
        }
        OrderSide::Sell => {
            let next_quantity = previous_quantity.checked_sub(fill_quantity)?;
            let closing_quantity = if previous_quantity.is_positive() {
                if fill_quantity.cmp_value(previous_quantity)? == std::cmp::Ordering::Greater {
                    previous_quantity
                } else {
                    fill_quantity
                }
            } else {
                SignedQuantity::ZERO
            };
            let realized_pnl = if closing_quantity.is_positive() && position.average_price.is_some()
            {
                fill.price
                    .checked_sub(previous_average)?
                    .checked_mul(closing_quantity)?
            } else {
                Money::ZERO
            };
            let next_average = if next_quantity.is_positive() {
                previous_average
            } else if next_quantity.is_negative() {
                fill.price
            } else {
                kairos_primitives::Price::new(1, 0).expect("positive fallback price")
            };
            (next_quantity, next_average, realized_pnl)
        }
    };
    position.quantity = next_quantity;
    position.average_price = if next_quantity.is_zero() {
        None
    } else {
        Some(next_average)
    };
    position.realized_pnl = Some(
        position
            .realized_pnl
            .unwrap_or(Money::ZERO)
            .checked_add(realized_pnl)?,
    );
    position.updated_at_unix_nanos = fill.occurred_at_unix_nanos;

    let mut balances = Vec::new();
    if let (Some(asset), Some(delta)) = (&fill.settlement_asset, fill.settlement_delta) {
        balances.push(balance_after_delta(account, asset, delta)?);
    }
    if let (Some(asset), Some(fee)) = (&fill.fee_asset, fill.fee_amount) {
        if fee.is_negative() {
            return Err(AccountDomainError::Invalid {
                field: "fill.fee_amount",
                reason: "must not be negative",
            });
        }
        let fee_delta = fee.checked_neg()?;
        if let Some(balance) = balances
            .iter_mut()
            .find(|balance| balance.asset_code.eq_ignore_ascii_case(asset))
        {
            balance.total = balance.total.checked_add(fee_delta)?;
        } else {
            balances.push(balance_after_delta(account, asset, fee_delta)?);
        }
    }

    Ok(AccountSnapshot {
        segment_key: account.segment_key.clone(),
        balances,
        collateral: Vec::new(),
        positions: vec![position],
        open_orders: Vec::new(),
        status: account.status,
        observed_at_unix_nanos: fill.occurred_at_unix_nanos.get().into(),
        equity: None,
        initial_equity: None,
        net_profit: None,
        account_model: None,
        margin_mode: None,
        position_mode: None,
        kind: SnapshotKind::Delta,
    })
}

fn balance_after_delta(
    account: &AccountSegmentView,
    asset: &str,
    delta: SignedQuantity,
) -> Result<Balance, AccountDomainError> {
    let asset_code = asset.trim().to_ascii_uppercase();
    if asset_code.is_empty() {
        return Err(AccountDomainError::Required {
            field: "fill.balance_asset",
        });
    }
    let asset_id = AssetId::new(format!("asset:{}", asset_code.to_ascii_lowercase()))?;
    let mut balance = account
        .balances
        .iter()
        .find(|balance| balance.asset_id == asset_id)
        .cloned()
        .unwrap_or(Balance {
            asset_id,
            asset_code: kairos_primitives::Currency::new(asset_code)?,
            total: SignedQuantity::ZERO,
            available: None,
            locked: None,
            borrowed: None,
            interest: None,
        });
    balance.total = balance.total.checked_add(delta)?;
    Ok(balance)
}
