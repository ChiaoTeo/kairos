//! Account-owned Contract codecs.

use std::collections::BTreeMap;

use flatbuffers::FlatBufferBuilder;
use kairos_account_contract::{
    ACCOUNT_BALANCES_DATABASE, ACCOUNT_COLLATERAL_DATABASE, ACCOUNT_EARN_HOLDINGS_DATABASE,
    ACCOUNT_OBSERVED_ORDERS_DATABASE, ACCOUNT_POSITIONS_DATABASE, ACCOUNT_SEGMENTS_DATABASE,
    ACCOUNT_VALUATIONS_DATABASE, EncodeContext, account_indexed_key, event_metadata,
};
use kairos_primitives::runtime::InstanceIdentity;
use kairos_protocol::generated::kairos::account::v_2 as account_fb;
use kairos_protocol::generated::kairos::common::v_2::{self as common_fb, Decimal64};

use crate::domain::{
    AccountBusinessChange, AccountBusinessEvent, AccountCurrentView, AccountModel,
    AccountSegmentCompleteness, AccountSegmentFreshness, AccountSegmentSyncLifecycle,
    AccountSegmentSyncMode, AccountSegmentView, AccountStatus, MarginMode, PositionMode,
};

pub(crate) fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .min(u64::MAX as u128) as u64
}

pub(crate) fn encode_indexed_current(
    view: &AccountCurrentView,
) -> Result<BTreeMap<(String, Vec<u8>), Vec<u8>>, String> {
    let mut values = BTreeMap::new();
    let account_id = view
        .segments
        .first()
        .ok_or_else(|| "Account view requires one account".to_owned())?
        .account_id
        .clone();
    if view
        .segments
        .iter()
        .any(|segment| segment.account_id != account_id)
    {
        return Err("Account view cannot contain multiple account ids".into());
    }
    for segment in &view.segments {
        let segment_key = segment.segment_key.as_str();
        insert_indexed(
            &mut values,
            ACCOUNT_SEGMENTS_DATABASE,
            &[segment_key],
            |builder| {
                let account_id = builder.create_string(account_id.as_str());
                let state = encode_segment(builder, segment)?;
                let root = account_fb::AccountSegmentCurrent::create(
                    builder,
                    &account_fb::AccountSegmentCurrentArgs {
                        account_id: Some(account_id),
                        state: Some(state),
                    },
                );
                account_fb::finish_account_segment_current_buffer(builder, root);
                Ok(())
            },
        )?;
        for balance in &segment.balances {
            insert_indexed(
                &mut values,
                ACCOUNT_BALANCES_DATABASE,
                &[segment_key, balance.asset_id.as_str()],
                |builder| {
                    let account_id = builder.create_string(account_id.as_str());
                    let segment_key = builder.create_string(segment_key);
                    let balance = encode_balance(builder, balance);
                    let root = account_fb::AccountBalanceCurrent::create(
                        builder,
                        &account_fb::AccountBalanceCurrentArgs {
                            account_id: Some(account_id),
                            segment_key: Some(segment_key),
                            balance: Some(balance),
                        },
                    );
                    account_fb::finish_account_balance_current_buffer(builder, root);
                    Ok(())
                },
            )?;
        }
        for balance in &segment.collateral {
            insert_indexed(
                &mut values,
                ACCOUNT_COLLATERAL_DATABASE,
                &[segment_key, balance.asset_id.as_str()],
                |builder| {
                    let account_id = builder.create_string(account_id.as_str());
                    let segment_key = builder.create_string(segment_key);
                    let balance = encode_balance(builder, balance);
                    let root = account_fb::AccountCollateralCurrent::create(
                        builder,
                        &account_fb::AccountCollateralCurrentArgs {
                            account_id: Some(account_id),
                            segment_key: Some(segment_key),
                            balance: Some(balance),
                        },
                    );
                    account_fb::finish_account_collateral_current_buffer(builder, root);
                    Ok(())
                },
            )?;
        }
        for position in &segment.positions {
            let side = position_side_key(position.position_side);
            insert_indexed(
                &mut values,
                ACCOUNT_POSITIONS_DATABASE,
                &[segment_key, position.instrument_id.as_str(), side],
                |builder| {
                    let account_id = builder.create_string(account_id.as_str());
                    let segment_key = builder.create_string(segment_key);
                    let position = encode_position(builder, position);
                    let root = account_fb::AccountPositionCurrent::create(
                        builder,
                        &account_fb::AccountPositionCurrentArgs {
                            account_id: Some(account_id),
                            segment_key: Some(segment_key),
                            position: Some(position),
                        },
                    );
                    account_fb::finish_account_position_current_buffer(builder, root);
                    Ok(())
                },
            )?;
        }
        if segment.equity.is_some()
            || segment.initial_equity.is_some()
            || segment.net_profit.is_some()
        {
            insert_indexed(
                &mut values,
                ACCOUNT_VALUATIONS_DATABASE,
                &[segment_key],
                |builder| {
                    let account_id = builder.create_string(account_id.as_str());
                    let segment_key = builder.create_string(segment_key);
                    let valuation =
                        encode_valuation(builder, segment)?.expect("valuation fields were checked");
                    let root = account_fb::AccountValuationCurrent::create(
                        builder,
                        &account_fb::AccountValuationCurrentArgs {
                            account_id: Some(account_id),
                            segment_key: Some(segment_key),
                            valuation: Some(valuation),
                        },
                    );
                    account_fb::finish_account_valuation_current_buffer(builder, root);
                    Ok(())
                },
            )?;
        }
        for holding in &segment.earn_holdings {
            let holding_key = holding
                .participant_position_id
                .as_deref()
                .unwrap_or(&holding.product_id);
            insert_indexed(
                &mut values,
                ACCOUNT_EARN_HOLDINGS_DATABASE,
                &[segment_key, holding_key],
                |builder| {
                    let account_id = builder.create_string(account_id.as_str());
                    let segment_key = builder.create_string(segment_key);
                    let holding = encode_earn_holding(builder, holding);
                    let root = account_fb::AccountEarnHoldingCurrent::create(
                        builder,
                        &account_fb::AccountEarnHoldingCurrentArgs {
                            account_id: Some(account_id),
                            segment_key: Some(segment_key),
                            holding: Some(holding),
                        },
                    );
                    account_fb::finish_account_earn_holding_current_buffer(builder, root);
                    Ok(())
                },
            )?;
        }
        let source_id = format!("account:{}", segment.broker);
        for order in &segment.open_orders {
            let order_id = order.order_id.to_string();
            insert_indexed(
                &mut values,
                ACCOUNT_OBSERVED_ORDERS_DATABASE,
                &[segment_key, &source_id, &order_id],
                |builder| {
                    let account_id = builder.create_string(account_id.as_str());
                    let segment_key = builder.create_string(segment_key);
                    let order = encode_observed_order(
                        builder,
                        &source_id,
                        order,
                        segment.observed_at_unix_nanos.get(),
                    )?;
                    let root = account_fb::AccountObservedOrderCurrent::create(
                        builder,
                        &account_fb::AccountObservedOrderCurrentArgs {
                            account_id: Some(account_id),
                            segment_key: Some(segment_key),
                            order: Some(order),
                        },
                    );
                    account_fb::finish_account_observed_order_current_buffer(builder, root);
                    Ok(())
                },
            )?;
        }
    }
    Ok(values)
}

fn insert_indexed(
    values: &mut BTreeMap<(String, Vec<u8>), Vec<u8>>,
    database: &str,
    parts: &[&str],
    encode: impl FnOnce(&mut FlatBufferBuilder<'_>) -> Result<(), String>,
) -> Result<(), String> {
    let key = account_indexed_key(parts).map_err(|error| error.to_string())?;
    let mut builder = FlatBufferBuilder::new();
    encode(&mut builder)?;
    values.insert((database.to_owned(), key), builder.finished_data().to_vec());
    Ok(())
}

fn encode_segment<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    account: &AccountSegmentView,
) -> Result<flatbuffers::WIPOffset<account_fb::AccountSegmentState<'a>>, String> {
    let segment_key = builder.create_string(account.segment_key.as_str());
    let environment = builder.create_string(&account.environment);
    let broker = builder.create_string(&account.broker);
    let last_error = account
        .last_error
        .as_deref()
        .map(|value| builder.create_string(value));
    Ok(account_fb::AccountSegmentState::create(
        builder,
        &account_fb::AccountSegmentStateArgs {
            segment_key: Some(segment_key),
            environment: Some(environment),
            broker: Some(broker),
            configured_account_model: account
                .configured_account_model
                .as_deref()
                .map(|value| account_model_name(value))
                .unwrap_or(account_fb::AccountModel::UNSPECIFIED),
            observed_account_model: account
                .observed_account_model
                .map(account_model)
                .unwrap_or(account_fb::AccountModel::UNSPECIFIED),
            status: account_status(account.status),
            freshness: segment_freshness(account.freshness),
            sync_mode: segment_sync_mode(account.sync_mode),
            sync_lifecycle: segment_sync_lifecycle(account.sync_lifecycle),
            completeness: segment_completeness(account.completeness),
            snapshot_watermark: account.snapshot_watermark.unwrap_or_default().get(),
            event_watermark: account.event_watermark.unwrap_or_default().get(),
            channel_epoch: account.channel_epoch.unwrap_or_default().get(),
            last_event_at_unix_nanos: account.last_event_at_unix_nanos.unwrap_or_default().get(),
            last_success_at_unix_nanos: account
                .last_success_at_unix_nanos
                .unwrap_or_default()
                .get(),
            last_error,
            recovery_buffer_depth: account.recovery_buffer_depth,
            observed_at_unix_nanos: account.observed_at_unix_nanos.get(),
            state_generation: account.generation.get(),
            earn_watermark_unix_nanos: account.earn_watermark_unix_nanos.get(),
            margin_mode: account
                .margin_mode
                .map(margin_mode)
                .unwrap_or(account_fb::MarginMode::UNSPECIFIED),
            position_mode: account
                .position_mode
                .map(position_mode)
                .unwrap_or(account_fb::PositionMode::UNSPECIFIED),
        },
    ))
}

fn encode_earn_holding<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    value: &crate::domain::EarnHolding,
) -> flatbuffers::WIPOffset<account_fb::EarnHolding<'a>> {
    let holding_key_value = value
        .participant_position_id
        .as_deref()
        .unwrap_or(value.product_id.as_str());
    let holding_key = builder.create_string(holding_key_value);
    let participant_position_id = value
        .participant_position_id
        .as_deref()
        .map(|value| builder.create_string(value));
    let product_id = builder.create_string(value.product_id.as_str());
    let asset = builder.create_string(value.asset.as_str());
    let principal = Decimal64::new(value.principal.mantissa(), value.principal.scale());
    let redeemable = value
        .redeemable
        .map(|value| Decimal64::new(value.mantissa(), value.scale()));
    let (state, participant_state) = match &value.state {
        crate::domain::EarnHoldingState::Active => (account_fb::EarnHoldingState::ACTIVE, None),
        crate::domain::EarnHoldingState::Redeeming => {
            (account_fb::EarnHoldingState::REDEEMING, None)
        },
        crate::domain::EarnHoldingState::Redeemed => (account_fb::EarnHoldingState::REDEEMED, None),
        crate::domain::EarnHoldingState::Unknown(raw) => (
            account_fb::EarnHoldingState::UNKNOWN,
            Some(builder.create_string(raw)),
        ),
    };
    let (liquidity, notice_seconds, matures_at) = match value.liquidity {
        crate::domain::EarnHoldingLiquidity::Immediate => {
            (account_fb::EarnLiquidity::IMMEDIATE, 0, 0)
        },
        crate::domain::EarnHoldingLiquidity::Notice { notice_seconds } => {
            (account_fb::EarnLiquidity::NOTICE, notice_seconds, 0)
        },
        crate::domain::EarnHoldingLiquidity::FixedTerm {
            matures_at_unix_nanos,
        } => (
            account_fb::EarnLiquidity::FIXED_TERM,
            0,
            matures_at_unix_nanos.get(),
        ),
        crate::domain::EarnHoldingLiquidity::Unknown => (account_fb::EarnLiquidity::UNKNOWN, 0, 0),
    };
    account_fb::EarnHolding::create(
        builder,
        &account_fb::EarnHoldingArgs {
            holding_key: Some(holding_key),
            participant_position_id,
            product_id: Some(product_id),
            asset: Some(asset),
            principal: Some(&principal),
            redeemable: redeemable.as_ref(),
            state,
            participant_state,
            liquidity,
            notice_seconds,
            matures_at_unix_nanos: matures_at,
            observed_at_unix_nanos: value.observed_at_unix_nanos.get(),
        },
    )
}

fn encode_valuation<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    account: &AccountSegmentView,
) -> Result<Option<flatbuffers::WIPOffset<account_fb::AccountValuation<'a>>>, String> {
    if account.equity.is_none() && account.initial_equity.is_none() && account.net_profit.is_none()
    {
        return Ok(None);
    }
    let valuation_asset_id = builder.create_string("");
    let equity = account
        .equity
        .map(|value| Decimal64::new(value.mantissa(), value.scale()));
    let initial_equity = account
        .initial_equity
        .map(|value| Decimal64::new(value.mantissa(), value.scale()));
    let net_profit = account
        .net_profit
        .map(|value| Decimal64::new(value.mantissa(), value.scale()));
    Ok(Some(account_fb::AccountValuation::create(
        builder,
        &account_fb::AccountValuationArgs {
            valuation_asset_id: Some(valuation_asset_id),
            equity: equity.as_ref(),
            initial_equity: initial_equity.as_ref(),
            net_profit: net_profit.as_ref(),
            observed_at_unix_nanos: account.observed_at_unix_nanos.get(),
        },
    )))
}

pub(crate) fn encode_business_change(
    owner_actor_id: &str,
    producer_incarnation: u64,
    identity: &InstanceIdentity,
    event: &AccountBusinessEvent,
    index: usize,
    change: &AccountBusinessChange,
) -> Result<Vec<u8>, String> {
    let mut builder = FlatBufferBuilder::new();
    let runtime_id = format!("account:{}", event.account_id);
    let context = EncodeContext::event(
        owner_actor_id,
        producer_incarnation,
        runtime_id,
        identity.clone(),
        event.sequence.get(),
        format!("account:{}:{}", event.sequence.get(), index),
    )?;
    let metadata = event_metadata(&mut builder, &context, event.occurred_at_unix_nanos.get());
    let account_id = builder.create_string(event.account_id.as_str());
    let provenance = event
        .provenance
        .as_ref()
        .map(|value| encode_provenance(&mut builder, value));
    match change {
        AccountBusinessChange::Balance { segment_key, value } => {
            let segment_key = builder.create_string(segment_key.as_str());
            let balance = encode_balance(&mut builder, value);
            let root = account_fb::BalanceUpserted::create(
                &mut builder,
                &account_fb::BalanceUpsertedArgs {
                    metadata: Some(metadata),
                    account_id: Some(account_id),
                    segment_key: Some(segment_key),
                    balance: Some(balance),
                    provenance,
                },
            );
            account_fb::finish_balance_upserted_buffer(&mut builder, root);
        },
        AccountBusinessChange::BalanceRemoved {
            segment_key,
            asset_id,
        } => {
            let segment_key = builder.create_string(segment_key.as_str());
            let asset_id = builder.create_string(asset_id.as_str());
            let root = account_fb::BalanceRemoved::create(
                &mut builder,
                &account_fb::BalanceRemovedArgs {
                    metadata: Some(metadata),
                    account_id: Some(account_id),
                    segment_key: Some(segment_key),
                    asset_id: Some(asset_id),
                    provenance,
                },
            );
            account_fb::finish_balance_removed_buffer(&mut builder, root);
        },
        AccountBusinessChange::Position { segment_key, value } => {
            let segment_key = builder.create_string(segment_key.as_str());
            let position = encode_position(&mut builder, value);
            let root = account_fb::PositionUpserted::create(
                &mut builder,
                &account_fb::PositionUpsertedArgs {
                    metadata: Some(metadata),
                    account_id: Some(account_id),
                    segment_key: Some(segment_key),
                    position: Some(position),
                    provenance,
                },
            );
            account_fb::finish_position_upserted_buffer(&mut builder, root);
        },
        AccountBusinessChange::PositionRemoved {
            segment_key,
            instrument_id,
            market_id,
            position_side,
        } => {
            let segment_key = builder.create_string(segment_key.as_str());
            let instrument_id = builder.create_string(instrument_id.as_str());
            let market_id_value = market_id
                .as_ref()
                .map(ToString::to_string)
                .unwrap_or_default();
            let market_id = builder.create_string(&market_id_value);
            let root = account_fb::PositionRemoved::create(
                &mut builder,
                &account_fb::PositionRemovedArgs {
                    metadata: Some(metadata),
                    account_id: Some(account_id),
                    segment_key: Some(segment_key),
                    instrument_id: Some(instrument_id),
                    market_id: Some(market_id),
                    position_side: encode_position_side(*position_side),
                    provenance,
                },
            );
            account_fb::finish_position_removed_buffer(&mut builder, root);
        },
        AccountBusinessChange::EarnHolding { segment_key, value } => {
            let segment_key = builder.create_string(segment_key.as_str());
            let holding = encode_earn_holding(&mut builder, value);
            let root = account_fb::EarnHoldingUpserted::create(
                &mut builder,
                &account_fb::EarnHoldingUpsertedArgs {
                    metadata: Some(metadata),
                    account_id: Some(account_id),
                    segment_key: Some(segment_key),
                    holding: Some(holding),
                    provenance,
                },
            );
            account_fb::finish_earn_holding_upserted_buffer(&mut builder, root);
        },
        AccountBusinessChange::EarnHoldingRemoved {
            segment_key,
            holding_key,
        } => {
            let segment_key = builder.create_string(segment_key.as_str());
            let holding_key = builder.create_string(holding_key);
            let root = account_fb::EarnHoldingRemoved::create(
                &mut builder,
                &account_fb::EarnHoldingRemovedArgs {
                    metadata: Some(metadata),
                    account_id: Some(account_id),
                    segment_key: Some(segment_key),
                    holding_key: Some(holding_key),
                    provenance,
                },
            );
            account_fb::finish_earn_holding_removed_buffer(&mut builder, root);
        },
        AccountBusinessChange::ObservedOrder { segment_key, value } => {
            let segment_key = builder.create_string(segment_key.as_str());
            let order = encode_observed_order(
                &mut builder,
                owner_actor_id,
                value,
                event.occurred_at_unix_nanos.get(),
            )?;
            let root = account_fb::ObservedOrderUpserted::create(
                &mut builder,
                &account_fb::ObservedOrderUpsertedArgs {
                    metadata: Some(metadata),
                    account_id: Some(account_id),
                    segment_key: Some(segment_key),
                    order: Some(order),
                    provenance,
                },
            );
            account_fb::finish_observed_order_upserted_buffer(&mut builder, root);
        },
        AccountBusinessChange::ObservedOrderRemoved {
            segment_key,
            order_id,
            remote_order_id,
        } => {
            let segment_key = builder.create_string(segment_key.as_str());
            let observation_id = builder.create_string(&order_id.to_string());
            let execution_order_id = builder.create_string(&order_id.to_string());
            let remote_order_id_value = remote_order_id
                .as_ref()
                .map(ToString::to_string)
                .unwrap_or_default();
            let remote_order_id = builder.create_string(&remote_order_id_value);
            let root = account_fb::ObservedOrderRemoved::create(
                &mut builder,
                &account_fb::ObservedOrderRemovedArgs {
                    metadata: Some(metadata),
                    account_id: Some(account_id),
                    segment_key: Some(segment_key),
                    observation_id: Some(observation_id),
                    execution_order_id: Some(execution_order_id),
                    remote_order_id: Some(remote_order_id),
                    provenance,
                },
            );
            account_fb::finish_observed_order_removed_buffer(&mut builder, root);
        },
        AccountBusinessChange::Equity { segment_key, value } => {
            let segment_key = builder.create_string(segment_key.as_str());
            let valuation =
                encode_event_valuation(&mut builder, *value, event.occurred_at_unix_nanos.get());
            let root = account_fb::ValuationChanged::create(
                &mut builder,
                &account_fb::ValuationChangedArgs {
                    metadata: Some(metadata),
                    account_id: Some(account_id),
                    segment_key: Some(segment_key),
                    valuation: Some(valuation),
                    provenance,
                },
            );
            account_fb::finish_valuation_changed_buffer(&mut builder, root);
        },
        AccountBusinessChange::Status {
            segment_key,
            status,
            stale,
        } => {
            let segment_key = builder.create_string(segment_key.as_str());
            let root = account_fb::AccountStatusChanged::create(
                &mut builder,
                &account_fb::AccountStatusChangedArgs {
                    metadata: Some(metadata),
                    account_id: Some(account_id),
                    segment_key: Some(segment_key),
                    status: account_status(*status),
                    freshness: if *stale {
                        account_fb::FreshnessState::STALE
                    } else {
                        account_fb::FreshnessState::FRESH
                    },
                    reason: None,
                    provenance,
                },
            );
            account_fb::finish_account_status_changed_buffer(&mut builder, root);
        },
    }
    Ok(builder.finished_data().to_vec())
}

fn encode_balance<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    value: &crate::domain::Balance,
) -> flatbuffers::WIPOffset<account_fb::Balance<'a>> {
    let asset_id = builder.create_string(value.asset_id.as_str());
    let asset_code = builder.create_string(value.asset_code.as_str());
    let total = Decimal64::new(value.total.mantissa(), value.total.scale());
    let available = value
        .available
        .map(|value| Decimal64::new(value.mantissa(), value.scale()));
    let locked = value
        .locked
        .map(|value| Decimal64::new(value.mantissa(), value.scale()));
    let borrowed = value
        .borrowed
        .map(|value| Decimal64::new(value.mantissa(), value.scale()));
    let interest = value
        .interest
        .map(|value| Decimal64::new(value.mantissa(), value.scale()));
    account_fb::Balance::create(
        builder,
        &account_fb::BalanceArgs {
            asset_id: Some(asset_id),
            asset_code: Some(asset_code),
            total: Some(&total),
            available: available.as_ref(),
            locked: locked.as_ref(),
            borrowed: borrowed.as_ref(),
            interest: interest.as_ref(),
        },
    )
}

fn encode_position<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    value: &crate::domain::Position,
) -> flatbuffers::WIPOffset<account_fb::Position<'a>> {
    let instrument_id = builder.create_string(value.instrument_id.as_str());
    let market_id_value = value
        .market_id
        .as_ref()
        .map(ToString::to_string)
        .unwrap_or_default();
    let market_id = builder.create_string(&market_id_value);
    let quantity = Decimal64::new(value.quantity.mantissa(), value.quantity.scale());
    let average_price = value
        .average_price
        .map(|value| Decimal64::new(value.mantissa(), value.scale()));
    let mark_price = value
        .mark_price
        .map(|value| Decimal64::new(value.mantissa(), value.scale()));
    let unrealized_pnl = value
        .unrealized_pnl
        .map(|value| Decimal64::new(value.mantissa(), value.scale()));
    let realized_pnl = value
        .realized_pnl
        .map(|value| Decimal64::new(value.mantissa(), value.scale()));
    account_fb::Position::create(
        builder,
        &account_fb::PositionArgs {
            instrument_id: Some(instrument_id),
            market_id: Some(market_id),
            position_side: encode_position_side(value.position_side),
            quantity: Some(&quantity),
            average_price: average_price.as_ref(),
            mark_price: mark_price.as_ref(),
            unrealized_pnl: unrealized_pnl.as_ref(),
            realized_pnl: realized_pnl.as_ref(),
            observed_at_unix_nanos: value.updated_at_unix_nanos.get(),
        },
    )
}

fn encode_position_side(
    value: kairos_primitives::account::PositionSide,
) -> account_fb::PositionSide {
    match value {
        kairos_primitives::account::PositionSide::Net => account_fb::PositionSide::NET,
        kairos_primitives::account::PositionSide::Long => account_fb::PositionSide::LONG,
        kairos_primitives::account::PositionSide::Short => account_fb::PositionSide::SHORT,
    }
}

fn position_side_key(value: kairos_primitives::account::PositionSide) -> &'static str {
    match value {
        kairos_primitives::account::PositionSide::Net => "NET",
        kairos_primitives::account::PositionSide::Long => "LONG",
        kairos_primitives::account::PositionSide::Short => "SHORT",
    }
}

fn encode_observed_order<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    source_id: &str,
    order: &crate::domain::OpenOrder,
    observed_at_unix_nanos: u64,
) -> Result<flatbuffers::WIPOffset<account_fb::ObservedOrder<'a>>, String> {
    let observation_id = builder.create_string(&order.order_id.to_string());
    let source_id = builder.create_string(source_id);
    let execution_order_id = builder.create_string(&order.order_id.to_string());
    let remote_order_id_value = order
        .remote_order_id
        .as_ref()
        .map(ToString::to_string)
        .unwrap_or_default();
    let remote_order_id = builder.create_string(&remote_order_id_value);
    let instrument_id = builder.create_string(order.instrument_id.as_str());
    let market_id_value = order
        .market_id
        .as_ref()
        .ok_or_else(|| {
            format!(
                "open order {} has no canonical market identity",
                order.order_id
            )
        })?
        .to_string();
    let market_id = builder.create_string(&market_id_value);
    let quantity = Decimal64::new(order.quantity.mantissa(), order.quantity.scale());
    let filled_quantity = Decimal64::new(
        order.filled_quantity.mantissa(),
        order.filled_quantity.scale(),
    );
    Ok(account_fb::ObservedOrder::create(
        builder,
        &account_fb::ObservedOrderArgs {
            observation_id: Some(observation_id),
            source_id: Some(source_id),
            execution_order_id: Some(execution_order_id),
            remote_order_id: Some(remote_order_id),
            instrument_id: Some(instrument_id),
            market_id: Some(market_id),
            side: if order.side == kairos_primitives::execution::OrderSide::Buy {
                common_fb::Side::BUY
            } else {
                common_fb::Side::SELL
            },
            quantity: Some(&quantity),
            filled_quantity: Some(&filled_quantity),
            status: observed_order_status(order.status),
            observed_at_unix_nanos,
        },
    ))
}

fn encode_event_valuation<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    equity: Option<crate::domain::Money>,
    observed_at_unix_nanos: u64,
) -> flatbuffers::WIPOffset<account_fb::AccountValuation<'a>> {
    let valuation_asset_id = builder.create_string("");
    let equity = equity.map(|value| Decimal64::new(value.mantissa(), value.scale()));
    account_fb::AccountValuation::create(
        builder,
        &account_fb::AccountValuationArgs {
            valuation_asset_id: Some(valuation_asset_id),
            equity: equity.as_ref(),
            initial_equity: None,
            net_profit: None,
            observed_at_unix_nanos,
        },
    )
}

fn encode_provenance<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    value: &crate::domain::AccountFactProvenance,
) -> flatbuffers::WIPOffset<account_fb::AccountFactProvenance<'a>> {
    let source_id = builder.create_string(&value.source_id);
    let provider_event_id = value
        .provider_event_id
        .as_deref()
        .map(|value| builder.create_string(value));
    account_fb::AccountFactProvenance::create(
        builder,
        &account_fb::AccountFactProvenanceArgs {
            source_id: Some(source_id),
            provider_event_id,
            provider_sequence: value.provider_sequence.map(|value| value.get()),
            provider_occurred_at_unix_nanos: value
                .provider_occurred_at_unix_nanos
                .map(|value| value.get()),
            provider_received_at_unix_nanos: value
                .provider_received_at_unix_nanos
                .map(|value| value.get()),
        },
    )
}

fn account_model(value: AccountModel) -> account_fb::AccountModel {
    match value {
        AccountModel::NoMargin => account_fb::AccountModel::CASH,
        AccountModel::Margin | AccountModel::Unified => account_fb::AccountModel::MARGIN,
        AccountModel::PortfolioMargin => account_fb::AccountModel::PORTFOLIO_MARGIN,
        AccountModel::Contract | AccountModel::ContractUnified => {
            account_fb::AccountModel::UNSPECIFIED
        },
    }
}

fn observed_order_status(
    value: kairos_primitives::integration::OrderStatus,
) -> account_fb::ObservedOrderStatus {
    match value {
        kairos_primitives::integration::OrderStatus::Acknowledged
        | kairos_primitives::integration::OrderStatus::Accepted => {
            account_fb::ObservedOrderStatus::OPEN
        },
        kairos_primitives::integration::OrderStatus::PartiallyFilled => {
            account_fb::ObservedOrderStatus::PARTIALLY_FILLED
        },
        kairos_primitives::integration::OrderStatus::Canceled
        | kairos_primitives::integration::OrderStatus::Filled
        | kairos_primitives::integration::OrderStatus::Rejected
        | kairos_primitives::integration::OrderStatus::Expired => {
            account_fb::ObservedOrderStatus::CLOSED
        },
        kairos_primitives::integration::OrderStatus::Pending => {
            account_fb::ObservedOrderStatus::OPEN
        },
        kairos_primitives::integration::OrderStatus::Unknown => {
            account_fb::ObservedOrderStatus::UNKNOWN
        },
    }
}

fn account_model_name(value: &str) -> account_fb::AccountModel {
    match value.to_ascii_lowercase().as_str() {
        "cash" | "no_margin" | "nomargin" => account_fb::AccountModel::CASH,
        "margin" => account_fb::AccountModel::MARGIN,
        "portfolio_margin" | "portfoliomargin" => account_fb::AccountModel::PORTFOLIO_MARGIN,
        _ => account_fb::AccountModel::UNSPECIFIED,
    }
}
fn account_status(value: AccountStatus) -> account_fb::AccountStatus {
    match value {
        AccountStatus::Ready => account_fb::AccountStatus::ACTIVE,
        AccountStatus::Reconciling => account_fb::AccountStatus::RECONCILING,
        AccountStatus::TypeMismatch => account_fb::AccountStatus::TYPE_MISMATCH,
        AccountStatus::Suspended => account_fb::AccountStatus::RESTRICTED,
        AccountStatus::Unavailable => account_fb::AccountStatus::UNAVAILABLE,
        AccountStatus::Unknown => account_fb::AccountStatus::UNSPECIFIED,
    }
}
fn margin_mode(value: MarginMode) -> account_fb::MarginMode {
    match value {
        MarginMode::Cross => account_fb::MarginMode::CROSS,
        MarginMode::Isolated => account_fb::MarginMode::ISOLATED,
    }
}
fn position_mode(value: PositionMode) -> account_fb::PositionMode {
    match value {
        PositionMode::OneWay => account_fb::PositionMode::NET,
        PositionMode::Hedge => account_fb::PositionMode::HEDGED,
    }
}

fn segment_freshness(value: AccountSegmentFreshness) -> account_fb::FreshnessState {
    match value {
        AccountSegmentFreshness::Fresh => account_fb::FreshnessState::FRESH,
        AccountSegmentFreshness::Stale => account_fb::FreshnessState::STALE,
        AccountSegmentFreshness::Resyncing => account_fb::FreshnessState::RESYNCING,
        AccountSegmentFreshness::Unavailable => account_fb::FreshnessState::UNAVAILABLE,
        AccountSegmentFreshness::Unknown => account_fb::FreshnessState::UNKNOWN,
    }
}

fn segment_sync_mode(value: AccountSegmentSyncMode) -> account_fb::SegmentSyncMode {
    match value {
        AccountSegmentSyncMode::Unknown => account_fb::SegmentSyncMode::UNSPECIFIED,
        AccountSegmentSyncMode::SnapshotThenStream => {
            account_fb::SegmentSyncMode::SNAPSHOT_THEN_STREAM
        },
        AccountSegmentSyncMode::SnapshotOnly => account_fb::SegmentSyncMode::SNAPSHOT_ONLY,
    }
}

fn segment_sync_lifecycle(value: AccountSegmentSyncLifecycle) -> account_fb::SegmentSyncLifecycle {
    match value {
        AccountSegmentSyncLifecycle::Configured => account_fb::SegmentSyncLifecycle::CONFIGURED,
        AccountSegmentSyncLifecycle::Bootstrapping => {
            account_fb::SegmentSyncLifecycle::BOOTSTRAPPING
        },
        AccountSegmentSyncLifecycle::Live => account_fb::SegmentSyncLifecycle::LIVE,
        AccountSegmentSyncLifecycle::SnapshotCurrent => {
            account_fb::SegmentSyncLifecycle::SNAPSHOT_CURRENT
        },
        AccountSegmentSyncLifecycle::Degraded => account_fb::SegmentSyncLifecycle::DEGRADED,
        AccountSegmentSyncLifecycle::Resyncing => account_fb::SegmentSyncLifecycle::RESYNCING,
        AccountSegmentSyncLifecycle::Unavailable => account_fb::SegmentSyncLifecycle::UNAVAILABLE,
        AccountSegmentSyncLifecycle::Stopped => account_fb::SegmentSyncLifecycle::STOPPED,
    }
}

fn segment_completeness(value: AccountSegmentCompleteness) -> account_fb::SegmentCompleteness {
    match value {
        AccountSegmentCompleteness::Complete => account_fb::SegmentCompleteness::COMPLETE,
        AccountSegmentCompleteness::Partial => account_fb::SegmentCompleteness::PARTIAL,
        AccountSegmentCompleteness::Unknown => account_fb::SegmentCompleteness::UNKNOWN,
    }
}

pub fn empty_snapshot(segment_key: impl Into<String>) -> crate::domain::AccountSnapshot {
    crate::domain::AccountSnapshot {
        segment_key: crate::domain::SegmentKey::new(segment_key.into())
            .expect("snapshot segment is required"),
        balances: Vec::new(),
        collateral: Vec::new(),
        positions: Vec::new(),
        open_orders: Vec::new(),
        status: AccountStatus::Ready,
        observed_at_unix_nanos: 0.into(),
        equity: None,
        initial_equity: None,
        net_profit: None,
        account_model: None,
        margin_mode: None,
        position_mode: None,
        kind: crate::domain::SnapshotKind::Full,
    }
}
