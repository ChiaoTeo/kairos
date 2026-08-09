use crate::application::AccountProjection;
use crate::domain::{
    AccountDomainError, AccountFill, AccountSnapshot, AssetId, Balance, Decimal, FillSide,
    Position, SegmentKey, SnapshotKind,
};

/// Builds authoritative account deltas for deterministic paper/simulated fills.
/// Live fills never use this calculation; live balances and positions come
/// from provider observations.
pub(crate) fn settle_paper_fill(
    account: &AccountProjection,
    fill: &AccountFill,
) -> Result<AccountSnapshot, AccountDomainError> {
    if account.segment_key != fill.segment_key {
        return Err(AccountDomainError::SegmentMismatch {
            expected: account.segment_key.clone(),
            observed: fill.segment_key.to_string(),
        });
    }

    let signed_quantity = match fill.side {
        FillSide::Buy => fill.quantity,
        FillSide::Sell => fill.quantity.checked_neg()?,
    };
    let mut position = account
        .positions
        .iter()
        .find(|position| position.instrument_id == fill.instrument_id)
        .cloned()
        .unwrap_or_else(|| Position {
            instrument_id: fill.instrument_id.clone(),
            market_id: None,
            quantity: Decimal::ZERO,
            average_price: None,
            mark_price: None,
            unrealized_pnl: None,
            realized_pnl: None,
            updated_at_unix_nanos: 0,
        });
    position.quantity = position.quantity.checked_add(signed_quantity)?;
    position.average_price = Some(fill.price);
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
        segment_key: SegmentKey::new(account.segment_key.clone())?,
        balances,
        collateral: Vec::new(),
        positions: vec![position],
        open_orders: Vec::new(),
        status: account.status,
        observed_at_unix_nanos: fill.occurred_at_unix_nanos,
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
    account: &AccountProjection,
    asset: &str,
    delta: Decimal,
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
            asset_code,
            total: Decimal::ZERO,
            available: None,
            locked: None,
            borrowed: None,
            interest: None,
        });
    balance.total = balance.total.checked_add(delta)?;
    Ok(balance)
}
