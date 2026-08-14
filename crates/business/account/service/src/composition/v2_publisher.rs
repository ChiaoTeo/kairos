use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use flatbuffers::FlatBufferBuilder;
use kairos_account_contract::{
    event_metadata, view_metadata, AccountAeronTransport, AccountViewKey, AccountViewKind,
    AccountViewPublisher, EncodeContext,
};
use kairos_protocol::generated::kairos::account::v_2 as account_fb;
use kairos_protocol::generated::kairos::common::v_2::{self as common_fb, Decimal64};
use kairos_protocol::InstanceIdentity;
use kairos_transport::AeronBytePublisher;

use crate::application::{
    AccountBusinessChange, AccountBusinessEvent, AccountProjection, AccountsSnapshot,
};
use crate::domain::{AccountModel, AccountStatus, MarginMode, PositionMode};

const DEFAULT_SLOT_SIZE: usize = 4 * 1024 * 1024;

pub struct FlatbuffersAccountPublisher {
    pub owner_actor_id: String,
    pub identity: InstanceIdentity,
    pub last_payload: Option<Vec<u8>>,
}

impl FlatbuffersAccountPublisher {
    pub fn new(owner_actor_id: impl Into<String>) -> Self {
        Self::new_with_identity(owner_actor_id, InstanceIdentity::default())
    }

    pub fn new_with_identity(
        owner_actor_id: impl Into<String>,
        identity: InstanceIdentity,
    ) -> Self {
        Self {
            owner_actor_id: owner_actor_id.into(),
            identity,
            last_payload: None,
        }
    }

    pub fn publish(&mut self, snapshot: &AccountsSnapshot) -> Result<(), String> {
        let bytes = encode_account_current_view(&self.owner_actor_id, &self.identity, snapshot)?;
        self.last_payload = Some(bytes);
        Ok(())
    }
}

pub struct FileAccountPublisher {
    pub path: PathBuf,
    pub inner: FlatbuffersAccountPublisher,
}

pub struct MmapAccountPublisher {
    root: PathBuf,
    slot_size: usize,
    owner_actor_id: String,
    identity: InstanceIdentity,
    writers: BTreeMap<String, AccountViewPublisher>,
}

impl MmapAccountPublisher {
    pub fn create(
        path: impl AsRef<Path>,
        slot_size: usize,
        owner_actor_id: impl Into<String>,
        identity: InstanceIdentity,
    ) -> Result<Self, String> {
        let path = path.as_ref();
        let root = path.parent().unwrap_or(path).join("snapshots").join("v2");
        std::fs::create_dir_all(&root).map_err(|error| error.to_string())?;
        Ok(Self {
            root,
            slot_size: if slot_size == 0 {
                DEFAULT_SLOT_SIZE
            } else {
                slot_size
            },
            owner_actor_id: owner_actor_id.into(),
            identity,
            writers: BTreeMap::new(),
        })
    }

    pub fn publish(&mut self, snapshot: &AccountsSnapshot) -> Result<(), String> {
        let account = snapshot
            .accounts
            .first()
            .ok_or_else(|| "Account view requires one account".to_owned())?;
        let runtime_id = format!("account:{}", account.account_id);
        let key = AccountViewKey::new(
            &runtime_id,
            account.account_id.to_string(),
            AccountViewKind::Current,
        )
        .map_err(|error| error.to_string())?;
        let bytes = encode_account_current_view(&self.owner_actor_id, &self.identity, snapshot)?;
        let resource_key = key.canonical_key();
        let writer = self.writers.entry(resource_key).or_insert_with(|| {
            AccountViewPublisher::create(&self.root, key.clone(), self.slot_size)
                .expect("validated Account v2 view resource must be creatable")
        });
        writer
            .publish(snapshot.generation.get(), &bytes)
            .map_err(|error| error.to_string())?;

        let orders_key = AccountViewKey::new(
            &runtime_id,
            account.account_id.to_string(),
            AccountViewKind::ObservedOrders,
        )
        .map_err(|error| error.to_string())?;
        let orders_bytes =
            encode_observed_orders_current_view(&self.owner_actor_id, &self.identity, snapshot)?;
        let orders_resource_key = orders_key.canonical_key();
        let orders_writer = self.writers.entry(orders_resource_key).or_insert_with(|| {
            AccountViewPublisher::create(&self.root, orders_key.clone(), self.slot_size)
                .expect("validated Account v2 observed-orders resource must be creatable")
        });
        orders_writer
            .publish(snapshot.generation.get(), &orders_bytes)
            .map_err(|error| error.to_string())
    }
}

pub struct AeronAccountEventPublisher {
    publisher: AeronBytePublisher,
    owner_actor_id: String,
    identity: InstanceIdentity,
}

impl AeronAccountEventPublisher {
    pub fn connect(
        aeron_dir: Option<&str>,
        channel: &str,
        stream_id: i32,
        owner_actor_id: impl Into<String>,
        identity: InstanceIdentity,
    ) -> Result<Self, String> {
        let owner_actor_id = owner_actor_id.into();
        let publisher = AccountAeronTransport::publisher(aeron_dir, channel, stream_id)
            .map_err(|error| error.to_string())?;
        Ok(Self {
            publisher,
            owner_actor_id,
            identity,
        })
    }

    pub fn publish(&mut self, event: &AccountBusinessEvent) -> Result<(), String> {
        for (index, change) in event.changes.iter().enumerate() {
            let bytes =
                encode_business_change(&self.owner_actor_id, &self.identity, event, index, change)?;
            self.publisher.publish(&bytes)?;
        }
        Ok(())
    }
}

fn encode_account_current_view(
    owner_actor_id: &str,
    identity: &InstanceIdentity,
    snapshot: &AccountsSnapshot,
) -> Result<Vec<u8>, String> {
    let account = snapshot
        .accounts
        .first()
        .ok_or_else(|| "Account view requires one account".to_owned())?;
    let runtime_id = format!("account:{}", account.account_id);
    let key = AccountViewKey::new(
        &runtime_id,
        account.account_id.to_string(),
        AccountViewKind::Current,
    )
    .map_err(|error| error.to_string())?;
    let mut builder = FlatBufferBuilder::new();
    let context = EncodeContext::view(
        owner_actor_id,
        owner_actor_id,
        &runtime_id,
        identity.clone(),
        snapshot.generation.get(),
        key.canonical_key(),
    );
    let metadata = view_metadata(
        &mut builder,
        &context,
        &key,
        account.observed_at_unix_nanos.get(),
    );
    let segment = encode_segment(&mut builder, account)?;
    let segments = builder.create_vector(&[segment]);
    let account_id = builder.create_string(account.account_id.as_str());
    let root = account_fb::AccountCurrentView::create(
        &mut builder,
        &account_fb::AccountCurrentViewArgs {
            metadata: Some(metadata),
            account_id: Some(account_id),
            segments: Some(segments),
        },
    );
    account_fb::finish_account_current_view_buffer(&mut builder, root);
    Ok(builder.finished_data().to_vec())
}

fn encode_observed_orders_current_view(
    owner_actor_id: &str,
    identity: &InstanceIdentity,
    snapshot: &AccountsSnapshot,
) -> Result<Vec<u8>, String> {
    let account = snapshot
        .accounts
        .first()
        .ok_or_else(|| "Account observed-orders view requires one account".to_owned())?;
    let runtime_id = format!("account:{}", account.account_id);
    let key = AccountViewKey::new(
        &runtime_id,
        account.account_id.to_string(),
        AccountViewKind::ObservedOrders,
    )
    .map_err(|error| error.to_string())?;
    let mut builder = FlatBufferBuilder::new();
    let context = EncodeContext::view(
        owner_actor_id,
        owner_actor_id,
        &runtime_id,
        identity.clone(),
        snapshot.generation.get(),
        key.canonical_key(),
    );
    let metadata = view_metadata(
        &mut builder,
        &context,
        &key,
        account.observed_at_unix_nanos.get(),
    );
    let segment_offsets = snapshot
        .accounts
        .iter()
        .map(|value| encode_observed_orders_segment(&mut builder, value))
        .collect::<Result<Vec<_>, _>>()?;
    let segments = builder.create_vector(&segment_offsets);
    let account_id = builder.create_string(account.account_id.as_str());
    let root = account_fb::ObservedOrdersCurrentView::create(
        &mut builder,
        &account_fb::ObservedOrdersCurrentViewArgs {
            metadata: Some(metadata),
            account_id: Some(account_id),
            segments: Some(segments),
        },
    );
    account_fb::finish_observed_orders_current_view_buffer(&mut builder, root);
    Ok(builder.finished_data().to_vec())
}

fn encode_observed_orders_segment<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    account: &AccountProjection,
) -> Result<flatbuffers::WIPOffset<account_fb::SegmentObservedOrders<'a>>, String> {
    let source_id = format!("account:{}", account.broker);
    let orders = account
        .open_orders
        .iter()
        .map(|order| {
            let observation_id = builder.create_string(&order.order_id.to_string());
            let source_id = builder.create_string(&source_id);
            let execution_order_id = builder.create_string(&order.order_id.to_string());
            let remote_order_id = order
                .remote_order_id
                .as_ref()
                .map(ToString::to_string)
                .unwrap_or_default();
            let remote_order_id = builder.create_string(&remote_order_id);
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
                    side: if order.side == kairos_domain_types::OrderSide::Buy {
                        common_fb::Side::BUY
                    } else {
                        common_fb::Side::SELL
                    },
                    quantity: Some(&quantity),
                    filled_quantity: Some(&filled_quantity),
                    status: observed_order_status(order.status),
                    observed_at_unix_nanos: account.observed_at_unix_nanos.get(),
                },
            ))
        })
        .collect::<Result<Vec<_>, String>>()?;
    let orders = builder.create_vector(&orders);
    let segment_key = builder.create_string(account.segment_key.as_str());
    Ok(account_fb::SegmentObservedOrders::create(
        builder,
        &account_fb::SegmentObservedOrdersArgs {
            segment_key: Some(segment_key),
            orders: Some(orders),
        },
    ))
}

fn encode_segment<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    account: &AccountProjection,
) -> Result<flatbuffers::WIPOffset<account_fb::AccountSegmentState<'a>>, String> {
    let segment_key = builder.create_string(account.segment_key.as_str());
    let environment = builder.create_string(&account.environment);
    let broker = builder.create_string(&account.broker);
    let balances = encode_balances(builder, &account.balances);
    let collateral = encode_balances(builder, &account.collateral);
    let positions = encode_positions(builder, &account.positions)?;
    let valuation = encode_valuation(builder, account)?;
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
            freshness: if account.stale {
                account_fb::FreshnessState::STALE
            } else {
                account_fb::FreshnessState::FRESH
            },
            observed_at_unix_nanos: account.observed_at_unix_nanos.get(),
            state_generation: account.generation.get(),
            valuation,
            balances: Some(balances),
            collateral: Some(collateral),
            positions: Some(positions),
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

fn encode_balances<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    values: &[crate::domain::Balance],
) -> flatbuffers::WIPOffset<
    flatbuffers::Vector<'a, flatbuffers::ForwardsUOffset<account_fb::Balance<'a>>>,
> {
    let offsets = values
        .iter()
        .map(|value| {
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
        })
        .collect::<Vec<_>>();
    builder.create_vector(&offsets)
}

fn encode_positions<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    values: &[crate::domain::Position],
) -> Result<
    flatbuffers::WIPOffset<
        flatbuffers::Vector<'a, flatbuffers::ForwardsUOffset<account_fb::Position<'a>>>,
    >,
    String,
> {
    let offsets = values
        .iter()
        .map(|value| {
            let instrument_id = builder.create_string(value.instrument_id.as_str());
            let market_id = builder.create_string(
                value
                    .market_id
                    .as_ref()
                    .map(ToString::to_string)
                    .as_deref()
                    .unwrap_or(""),
            );
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
                    quantity: Some(&quantity),
                    average_price: average_price.as_ref(),
                    mark_price: mark_price.as_ref(),
                    unrealized_pnl: unrealized_pnl.as_ref(),
                    realized_pnl: realized_pnl.as_ref(),
                    observed_at_unix_nanos: value.updated_at_unix_nanos.get(),
                },
            )
        })
        .collect::<Vec<_>>();
    Ok(builder.create_vector(&offsets))
}

fn encode_valuation<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    account: &AccountProjection,
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

fn encode_business_change(
    owner_actor_id: &str,
    identity: &InstanceIdentity,
    event: &AccountBusinessEvent,
    index: usize,
    change: &AccountBusinessChange,
) -> Result<Vec<u8>, String> {
    let mut builder = FlatBufferBuilder::new();
    let runtime_id = format!("account:{}", event.account_id);
    let context = EncodeContext::event(
        owner_actor_id,
        runtime_id,
        identity.clone(),
        event.sequence.get(),
        format!("account:{}:{}", event.sequence.get(), index),
    );
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
        }
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
        }
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
        }
        AccountBusinessChange::PositionRemoved {
            segment_key,
            instrument_id,
            market_id,
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
                    provenance,
                },
            );
            account_fb::finish_position_removed_buffer(&mut builder, root);
        }
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
        }
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
        }
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
        }
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
        }
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
    account_fb::Balance::create(
        builder,
        &account_fb::BalanceArgs {
            asset_id: Some(asset_id),
            asset_code: Some(asset_code),
            total: Some(&total),
            available: None,
            locked: None,
            borrowed: None,
            interest: None,
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
            quantity: Some(&quantity),
            average_price: average_price.as_ref(),
            mark_price: mark_price.as_ref(),
            unrealized_pnl: unrealized_pnl.as_ref(),
            realized_pnl: realized_pnl.as_ref(),
            observed_at_unix_nanos: value.updated_at_unix_nanos.get(),
        },
    )
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
            side: if order.side == kairos_domain_types::OrderSide::Buy {
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
    value: &crate::application::AccountFactProvenance,
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
            provider_sequence: value.provider_sequence,
            provider_occurred_at_unix_nanos: value.provider_occurred_at_unix_nanos,
            provider_received_at_unix_nanos: value.provider_received_at_unix_nanos,
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
        }
    }
}

fn observed_order_status(
    value: kairos_domain_types::OrderStatus,
) -> account_fb::ObservedOrderStatus {
    match value {
        kairos_domain_types::OrderStatus::Acknowledged
        | kairos_domain_types::OrderStatus::Accepted => account_fb::ObservedOrderStatus::OPEN,
        kairos_domain_types::OrderStatus::PartiallyFilled => {
            account_fb::ObservedOrderStatus::PARTIALLY_FILLED
        }
        kairos_domain_types::OrderStatus::Canceled
        | kairos_domain_types::OrderStatus::Filled
        | kairos_domain_types::OrderStatus::Rejected
        | kairos_domain_types::OrderStatus::Expired => account_fb::ObservedOrderStatus::CLOSED,
        kairos_domain_types::OrderStatus::Pending => account_fb::ObservedOrderStatus::OPEN,
        kairos_domain_types::OrderStatus::Unknown => account_fb::ObservedOrderStatus::UNKNOWN,
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

impl FileAccountPublisher {
    pub fn new(path: impl Into<PathBuf>, owner_actor_id: impl Into<String>) -> Self {
        Self::new_with_identity(path, owner_actor_id, InstanceIdentity::default())
    }
    pub fn new_with_identity(
        path: impl Into<PathBuf>,
        owner_actor_id: impl Into<String>,
        identity: InstanceIdentity,
    ) -> Self {
        Self {
            path: path.into(),
            inner: FlatbuffersAccountPublisher::new_with_identity(owner_actor_id, identity),
        }
    }
    pub fn publish(&mut self, snapshot: &AccountsSnapshot) -> Result<(), String> {
        self.inner.publish(snapshot)?;
        if let Some(parent) = self.path.parent() {
            std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        let temporary = self.path.with_extension("tmp");
        std::fs::write(
            &temporary,
            self.inner.last_payload.as_deref().unwrap_or_default(),
        )
        .map_err(|error| error.to_string())?;
        std::fs::rename(temporary, &self.path).map_err(|error| error.to_string())
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

impl crate::application::AccountSnapshotPublisher for MmapAccountPublisher {
    fn publish(&mut self, snapshot: &AccountsSnapshot) -> Result<(), String> {
        MmapAccountPublisher::publish(self, snapshot)
    }
}

impl crate::application::AccountEventPublisher for AeronAccountEventPublisher {
    fn publish(&mut self, event: &AccountBusinessEvent) -> Result<(), String> {
        AeronAccountEventPublisher::publish(self, event)
    }
}
