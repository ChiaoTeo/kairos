use std::time::{SystemTime, UNIX_EPOCH};

use flatbuffers::FlatBufferBuilder;
use kairos_protocol::generated::kairos::{
    account::v_1 as account_fb,
    common::v_1::{Decimal64, MessageHeader, MessageHeaderArgs},
};
use kairos_protocol::InstanceIdentity;

use crate::model::{AccountStatus, Balance, Decimal, Position};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AccountStrategyEvent {
    pub sequence: u64,
    pub account_id: String,
    pub occurred_at_unix_nanos: u64,
    pub changes: Vec<AccountStrategyChange>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum AccountStrategyChange {
    Balance {
        segment_key: String,
        value: Balance,
    },
    Position {
        segment_key: String,
        value: Position,
    },
    Equity {
        segment_key: String,
        value: Option<Decimal>,
    },
    Status {
        segment_key: String,
        status: AccountStatus,
        stale: bool,
    },
}

/// Account-owned Aeron publisher for facts already emitted by Account.
/// It never accepts, reads, or compares a state snapshot.
pub struct AeronAccountEventPublisher {
    publisher: kairos_transport::AeronBytePublisher,
    producer_id: String,
    identity: InstanceIdentity,
}

impl AeronAccountEventPublisher {
    pub fn connect(
        aeron_dir: Option<&str>,
        channel: &str,
        stream_id: i32,
        producer_id: impl Into<String>,
        identity: InstanceIdentity,
    ) -> Result<Self, String> {
        Ok(Self {
            publisher: kairos_transport::AeronBytePublisher::connect(
                aeron_dir, channel, stream_id,
            )?,
            producer_id: producer_id.into(),
            identity,
        })
    }

    pub fn publish(&mut self, event: &AccountStrategyEvent) -> Result<(), String> {
        self.publisher.publish(&encode_event(
            &self.producer_id,
            &self.identity,
            event.sequence,
            &event.account_id,
            event.occurred_at_unix_nanos,
            &event.changes,
        )?)
    }
}

fn encode_event(
    producer_id_value: &str,
    identity: &InstanceIdentity,
    sequence: u64,
    account_id_value: &str,
    occurred_at_unix_nanos: u64,
    changes: &[AccountStrategyChange],
) -> Result<Vec<u8>, String> {
    let publish_time_unix_nanos = now_unix_nanos();
    let mut builder = FlatBufferBuilder::new();
    let mut offsets = Vec::with_capacity(changes.len());
    for change in changes {
        offsets.push(encode_change(&mut builder, change));
    }
    let changes = builder.create_vector(&offsets);
    let message_id = builder.create_string(&format!("account:{account_id_value}:{sequence}"));
    // Account sequences are contiguous per account, so the logical stream
    // identity must carry the same scope. Multiple accounts may share one
    // physical Aeron publication without pretending to share one sequence.
    let stream_id = builder.create_string(&format!("account.events:{account_id_value}"));
    let producer_id = builder.create_string(producer_id_value);
    let workspace_id = non_empty_string(&mut builder, &identity.workspace_id);
    let launch_id = non_empty_string(&mut builder, &identity.launch_id);
    let instance_id = non_empty_string(&mut builder, &identity.instance_id);
    let header = MessageHeader::create(
        &mut builder,
        &MessageHeaderArgs {
            message_id: Some(message_id),
            stream_id: Some(stream_id),
            producer_id: Some(producer_id),
            workspace_id,
            launch_id,
            instance_id,
            sequence,
            event_time_unix_nanos: occurred_at_unix_nanos,
            publish_time_unix_nanos,
        },
    );
    let account_id = builder.create_string(account_id_value);
    let event = account_fb::AccountEvent::create(
        &mut builder,
        &account_fb::AccountEventArgs {
            header: Some(header),
            account_id: Some(account_id),
            changes: Some(changes),
            occurred_at_unix_nanos,
        },
    );
    account_fb::finish_account_event_buffer(&mut builder, event);
    Ok(builder.finished_data().to_vec())
}

fn encode_change<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    change: &AccountStrategyChange,
) -> flatbuffers::WIPOffset<account_fb::AccountChange<'a>> {
    match change {
        AccountStrategyChange::Balance { segment_key, value } => {
            let kind = builder.create_string("balance_changed");
            let segment_key = builder.create_string(segment_key);
            let asset_id = builder.create_string(&value.asset_id);
            let asset_code = builder.create_string(&value.asset_code);
            let total = decimal(value.total);
            let available = value.available.map(decimal);
            let locked = value.locked.map(decimal);
            let borrowed = value.borrowed.map(decimal);
            let interest = value.interest.map(decimal);
            let balance = account_fb::Balance::create(
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
            );
            account_fb::AccountChange::create(
                builder,
                &account_fb::AccountChangeArgs {
                    kind: Some(kind),
                    segment_key: Some(segment_key),
                    balance: Some(balance),
                    ..Default::default()
                },
            )
        }
        AccountStrategyChange::Position { segment_key, value } => {
            let kind = builder.create_string("position_changed");
            let segment_key = builder.create_string(segment_key);
            let instrument_id = builder.create_string(&value.instrument_id);
            let market_id = value
                .market_id
                .as_ref()
                .map(|value| builder.create_string(value));
            let quantity = decimal(value.quantity);
            let average_price = value.average_price.map(decimal);
            let mark_price = value.mark_price.map(decimal);
            let unrealized_pnl = value.unrealized_pnl.map(decimal);
            let realized_pnl = value.realized_pnl.map(decimal);
            let position = account_fb::Position::create(
                builder,
                &account_fb::PositionArgs {
                    instrument_id: Some(instrument_id),
                    market_id,
                    quantity: Some(&quantity),
                    average_price: average_price.as_ref(),
                    mark_price: mark_price.as_ref(),
                    unrealized_pnl: unrealized_pnl.as_ref(),
                    realized_pnl: realized_pnl.as_ref(),
                    updated_at_unix_nanos: value.updated_at_unix_nanos,
                },
            );
            account_fb::AccountChange::create(
                builder,
                &account_fb::AccountChangeArgs {
                    kind: Some(kind),
                    segment_key: Some(segment_key),
                    position: Some(position),
                    ..Default::default()
                },
            )
        }
        AccountStrategyChange::Equity { segment_key, value } => {
            let kind = builder.create_string("equity_changed");
            let segment_key = builder.create_string(segment_key);
            let equity = value.map(decimal);
            account_fb::AccountChange::create(
                builder,
                &account_fb::AccountChangeArgs {
                    kind: Some(kind),
                    segment_key: Some(segment_key),
                    equity: equity.as_ref(),
                    ..Default::default()
                },
            )
        }
        AccountStrategyChange::Status {
            segment_key,
            status,
            stale,
        } => {
            let kind = builder.create_string("status_changed");
            let segment_key = builder.create_string(segment_key);
            let status_value = builder.create_string(status.as_str());
            account_fb::AccountChange::create(
                builder,
                &account_fb::AccountChangeArgs {
                    kind: Some(kind),
                    segment_key: Some(segment_key),
                    status: Some(status_value),
                    stale: *stale,
                    trading_enabled: *status == AccountStatus::Ready && !stale,
                    ..Default::default()
                },
            )
        }
    }
}

fn decimal(value: Decimal) -> Decimal64 {
    Decimal64::new(value.mantissa, value.scale)
}

fn non_empty_string<'a, 'b, A: flatbuffers::Allocator + 'a>(
    builder: &'b mut FlatBufferBuilder<'a, A>,
    value: &str,
) -> Option<flatbuffers::WIPOffset<&'a str>> {
    (!value.is_empty()).then(|| builder.create_string(value))
}

fn now_unix_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .try_into()
        .unwrap_or(u64::MAX)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn encoded_changes_preserve_segment_identity() {
        let payload = encode_event(
            "account",
            &InstanceIdentity::default(),
            1,
            "main",
            10,
            &[AccountStrategyChange::Balance {
                segment_key: "spot".into(),
                value: Balance {
                    asset_id: "asset:usdt".into(),
                    asset_code: "USDT".into(),
                    total: Decimal {
                        mantissa: 100,
                        scale: 0,
                    },
                    available: None,
                    locked: None,
                    borrowed: None,
                    interest: None,
                },
            }],
        )
        .unwrap();

        let event = account_fb::root_as_account_event(&payload).unwrap();
        let changes = event.changes().unwrap();
        assert_eq!(changes.len(), 1);
        assert_eq!(changes.get(0).segment_key(), "spot");
    }
}
