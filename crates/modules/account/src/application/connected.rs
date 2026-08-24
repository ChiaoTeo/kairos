//! Account connected/runtime application facade.
//!
//! Connected CLI entry points use this facade to query Account runtime
//! current views or call typed Account runtime control. Standalone Account CLI
//! commands must use `CliAccountApplication`.

use std::path::PathBuf;

use kairos_account_contract::{
    AccountClient, AccountCommandStatus, AccountControlRpcClient, AccountRefreshResponse,
    AccountSegmentsRequest, SimulatedSettlement,
};
use kairos_protocol::generated::kairos::common::v_2::{Decimal64, ViewCompleteness};
use serde::Serialize;

#[derive(Debug, Serialize)]
pub struct AccountObservedOrdersResult {
    pub account_id: String,
    pub generation: u64,
    pub orders: Vec<AccountObservedOrderResult>,
}

#[derive(Debug, Serialize)]
pub struct AccountObservedOrderResult {
    pub segment_key: String,
    pub observation_id: String,
    pub source_id: String,
    pub execution_order_id: Option<String>,
    pub remote_order_id: Option<String>,
    pub instrument_id: String,
    pub market_id: String,
    pub side: String,
    pub quantity: String,
    pub filled_quantity: String,
    pub status: String,
    pub observed_at_unix_nanos: u64,
}

#[derive(Debug, Serialize)]
pub struct AccountCurrentResult {
    pub account_id: String,
    pub generation: u64,
    pub event_sequence: u64,
    pub producer_incarnation: u64,
    pub segments: Vec<AccountSegmentResult>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub page: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub page_size: Option<usize>,
}

#[derive(Debug, Serialize)]
pub struct AccountSegmentResult {
    pub segment_key: String,
    pub environment: String,
    pub broker: String,
    pub configured_account_model: String,
    pub observed_account_model: String,
    pub margin_mode: String,
    pub position_mode: String,
    pub status: String,
    pub freshness: String,
    pub sync_mode: String,
    pub sync_lifecycle: String,
    pub completeness: String,
    pub snapshot_watermark: u64,
    pub event_watermark: u64,
    pub channel_epoch: u64,
    pub last_event_at_unix_nanos: u64,
    pub last_success_at_unix_nanos: u64,
    pub last_error: Option<String>,
    pub recovery_buffer_depth: u64,
    pub observed_at_unix_nanos: u64,
    pub state_generation: u64,
    pub balances: Vec<AccountBalanceResult>,
    pub collateral: Vec<AccountBalanceResult>,
    pub positions: Vec<AccountPositionResult>,
    pub earn_holdings: Vec<AccountEarnHoldingResult>,
    pub earn_watermark_unix_nanos: u64,
}

#[derive(Debug, Serialize)]
pub struct AccountBalanceResult {
    pub asset_id: String,
    pub asset_code: Option<String>,
    pub total: String,
    pub available: Option<String>,
    pub locked: Option<String>,
    pub borrowed: Option<String>,
    pub interest: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct AccountPositionResult {
    pub instrument_id: String,
    pub market_id: String,
    pub quantity: String,
    pub average_price: Option<String>,
    pub mark_price: Option<String>,
    pub unrealized_pnl: Option<String>,
    pub realized_pnl: Option<String>,
    pub observed_at_unix_nanos: u64,
}

#[derive(Debug, Serialize)]
pub struct AccountEarnHoldingResult {
    pub holding_key: String,
    pub participant_position_id: Option<String>,
    pub product_id: String,
    pub asset: String,
    pub principal: String,
    pub redeemable: Option<String>,
    pub state: String,
    pub participant_state: Option<String>,
    pub liquidity: String,
    pub notice_seconds: u64,
    pub matures_at_unix_nanos: u64,
    pub observed_at_unix_nanos: u64,
}

pub struct ConnectedAccountApplication {
    client: AccountClient,
}

#[derive(Debug, Serialize)]
#[serde(untagged)]
pub enum ConnectedAccountOutput {
    Command(AccountCommandStatus),
    Refresh(AccountRefreshResponse),
    Current(AccountCurrentResult),
    ObservedOrders(AccountObservedOrdersResult),
}

impl ConnectedAccountApplication {
    pub fn connect(
        socket: PathBuf,
        view_root: Option<PathBuf>,
    ) -> Result<Self, Box<dyn std::error::Error>> {
        let mut system = kairos_conflux::ConfluxSystem::new();
        system.install_account_connection("account", socket, view_root)?;
        let client = system
            .account_client("account")
            .ok_or("managed Account client is missing: account")?;
        Ok(Self { client })
    }

    pub async fn apply_simulated_settlement(
        &self,
        settlement: SimulatedSettlement,
    ) -> Result<AccountCommandStatus, Box<dyn std::error::Error>> {
        let status =
            AccountControlRpcClient::apply_simulated_settlement(&self.client.control(), settlement)
                .await?;
        Ok(status)
    }

    pub async fn refresh(
        &self,
        request: AccountSegmentsRequest,
    ) -> Result<AccountRefreshResponse, Box<dyn std::error::Error>> {
        let response = AccountControlRpcClient::refresh(&self.client.control(), request).await?;
        Ok(response)
    }

    pub async fn reconcile(
        &self,
        request: AccountSegmentsRequest,
    ) -> Result<AccountRefreshResponse, Box<dyn std::error::Error>> {
        let response = AccountControlRpcClient::reconcile(&self.client.control(), request).await?;
        Ok(response)
    }

    pub fn snapshot(
        &self,
        account_id: &str,
        symbol: Option<&str>,
    ) -> Result<AccountCurrentResult, Box<dyn std::error::Error>> {
        self.current(account_id, &[], symbol, false, None)
    }

    pub fn balances(
        &self,
        account_id: &str,
        segments: &[String],
        include_zero: bool,
        page: usize,
        page_size: usize,
    ) -> Result<AccountCurrentResult, Box<dyn std::error::Error>> {
        self.current(
            account_id,
            segments,
            None,
            include_zero,
            Some((page, page_size)),
        )
    }

    pub fn positions(
        &self,
        account_id: &str,
        segments: &[String],
        symbol: Option<&str>,
    ) -> Result<AccountCurrentResult, Box<dyn std::error::Error>> {
        self.current(account_id, segments, symbol, false, None)
    }

    pub fn observed_orders(
        &self,
        account_id: &str,
        symbol: Option<&str>,
        limit: Option<usize>,
    ) -> Result<AccountObservedOrdersResult, Box<dyn std::error::Error>> {
        let frame = self
            .client
            .observed_orders(format!("account:{account_id}"), account_id)?
            .read()?;
        let view = frame.view()?;
        let metadata = view.metadata();
        if view.account_id() != account_id
            || metadata.completeness() != ViewCompleteness::COMPLETE
            || metadata.generation() != frame.generation()
            || metadata.applied_revision() != Some(frame.envelope_metadata().applied_event_sequence)
        {
            return Err(
                "Account observed-orders mmap identity, completeness, or watermark mismatch".into(),
            );
        }
        let mut orders = Vec::new();
        for segment in view.segments() {
            for order in segment.orders() {
                if symbol.is_some_and(|needle| {
                    !order.instrument_id().eq_ignore_ascii_case(needle)
                        && !order.market_id().eq_ignore_ascii_case(needle)
                }) {
                    continue;
                }
                orders.push(AccountObservedOrderResult {
                    segment_key: segment.segment_key().to_owned(),
                    observation_id: order.observation_id().to_owned(),
                    source_id: order.source_id().to_owned(),
                    execution_order_id: order.execution_order_id().map(str::to_owned),
                    remote_order_id: order.remote_order_id().map(str::to_owned),
                    instrument_id: order.instrument_id().to_owned(),
                    market_id: order.market_id().to_owned(),
                    side: enum_name(order.side().variant_name()),
                    quantity: decimal_text(order.quantity()),
                    filled_quantity: decimal_text(order.filled_quantity()),
                    status: enum_name(order.status().variant_name()),
                    observed_at_unix_nanos: order.observed_at_unix_nanos(),
                });
                if limit.is_some_and(|limit| orders.len() >= limit) {
                    break;
                }
            }
            if limit.is_some_and(|limit| orders.len() >= limit) {
                break;
            }
        }
        Ok(AccountObservedOrdersResult {
            account_id: account_id.to_owned(),
            generation: frame.generation(),
            orders,
        })
    }

    fn current(
        &self,
        account_id: &str,
        segment_filter: &[String],
        symbol_filter: Option<&str>,
        include_zero: bool,
        balance_page: Option<(usize, usize)>,
    ) -> Result<AccountCurrentResult, Box<dyn std::error::Error>> {
        let frame = self
            .client
            .account_current(format!("account:{account_id}"), account_id)?
            .read()?;
        let view = frame.view()?;
        let metadata = view.metadata();
        if view.account_id() != account_id
            || metadata.completeness() != ViewCompleteness::COMPLETE
            || metadata.generation() != frame.generation()
            || metadata.applied_revision() != Some(frame.envelope_metadata().applied_event_sequence)
        {
            return Err(
                "Account current mmap identity, completeness, or watermark mismatch".into(),
            );
        }
        let mut segments = Vec::new();
        for segment in view.segments() {
            if !segment_filter.is_empty()
                && !segment_filter
                    .iter()
                    .any(|value| value == segment.segment_key())
            {
                continue;
            }
            let balances = segment
                .balances()
                .iter()
                .filter(|balance| include_zero || balance.total().mantissa() != 0)
                .map(|balance| AccountBalanceResult {
                    asset_id: balance.asset_id().to_owned(),
                    asset_code: balance.asset_code().map(str::to_owned),
                    total: decimal_text(balance.total()),
                    available: optional_decimal(balance.available()),
                    locked: optional_decimal(balance.locked()),
                    borrowed: optional_decimal(balance.borrowed()),
                    interest: optional_decimal(balance.interest()),
                })
                .collect::<Vec<_>>();
            let collateral = segment
                .collateral()
                .iter()
                .filter(|balance| include_zero || balance.total().mantissa() != 0)
                .map(|balance| AccountBalanceResult {
                    asset_id: balance.asset_id().to_owned(),
                    asset_code: balance.asset_code().map(str::to_owned),
                    total: decimal_text(balance.total()),
                    available: optional_decimal(balance.available()),
                    locked: optional_decimal(balance.locked()),
                    borrowed: optional_decimal(balance.borrowed()),
                    interest: optional_decimal(balance.interest()),
                })
                .collect::<Vec<_>>();
            let positions = segment
                .positions()
                .iter()
                .filter(|position| {
                    symbol_filter.is_none_or(|needle| {
                        position.instrument_id().eq_ignore_ascii_case(needle)
                            || position.market_id().eq_ignore_ascii_case(needle)
                    })
                })
                .map(|position| AccountPositionResult {
                    instrument_id: position.instrument_id().to_owned(),
                    market_id: position.market_id().to_owned(),
                    quantity: decimal_text(position.quantity()),
                    average_price: optional_decimal(position.average_price()),
                    mark_price: optional_decimal(position.mark_price()),
                    unrealized_pnl: optional_decimal(position.unrealized_pnl()),
                    realized_pnl: optional_decimal(position.realized_pnl()),
                    observed_at_unix_nanos: position.observed_at_unix_nanos(),
                })
                .collect::<Vec<_>>();
            let earn_holdings = segment
                .earn_holdings()
                .iter()
                .map(|holding| AccountEarnHoldingResult {
                    holding_key: holding.holding_key().to_owned(),
                    participant_position_id: holding.participant_position_id().map(str::to_owned),
                    product_id: holding.product_id().to_owned(),
                    asset: holding.asset().to_owned(),
                    principal: decimal_text(holding.principal()),
                    redeemable: optional_decimal(holding.redeemable()),
                    state: enum_name(holding.state().variant_name()),
                    participant_state: holding.participant_state().map(str::to_owned),
                    liquidity: enum_name(holding.liquidity().variant_name()),
                    notice_seconds: holding.notice_seconds(),
                    matures_at_unix_nanos: holding.matures_at_unix_nanos(),
                    observed_at_unix_nanos: holding.observed_at_unix_nanos(),
                })
                .collect::<Vec<_>>();
            segments.push(AccountSegmentResult {
                segment_key: segment.segment_key().to_owned(),
                environment: segment.environment().to_owned(),
                broker: segment.broker().to_owned(),
                configured_account_model: enum_name(
                    segment.configured_account_model().variant_name(),
                ),
                observed_account_model: enum_name(segment.observed_account_model().variant_name()),
                margin_mode: enum_name(segment.margin_mode().variant_name()),
                position_mode: enum_name(segment.position_mode().variant_name()),
                status: enum_name(segment.status().variant_name()),
                freshness: enum_name(segment.freshness().variant_name()),
                sync_mode: enum_name(segment.sync_mode().variant_name()),
                sync_lifecycle: enum_name(segment.sync_lifecycle().variant_name()),
                completeness: enum_name(segment.completeness().variant_name()),
                snapshot_watermark: segment.snapshot_watermark(),
                event_watermark: segment.event_watermark(),
                channel_epoch: segment.channel_epoch(),
                last_event_at_unix_nanos: segment.last_event_at_unix_nanos(),
                last_success_at_unix_nanos: segment.last_success_at_unix_nanos(),
                last_error: segment.last_error().map(str::to_owned),
                recovery_buffer_depth: segment.recovery_buffer_depth(),
                observed_at_unix_nanos: segment.observed_at_unix_nanos(),
                state_generation: segment.state_generation(),
                balances,
                collateral,
                positions,
                earn_holdings,
                earn_watermark_unix_nanos: segment.earn_watermark_unix_nanos(),
            });
        }
        let (page, page_size) = balance_page.map_or((None, None), |(page, page_size)| {
            (Some(page), Some(page_size))
        });
        Ok(AccountCurrentResult {
            account_id: account_id.to_owned(),
            generation: frame.generation(),
            event_sequence: frame.envelope_metadata().applied_event_sequence,
            producer_incarnation: frame.envelope_metadata().producer_incarnation,
            segments,
            page,
            page_size,
        })
    }
}

fn decimal_text(value: &Decimal64) -> String {
    let scale = value.scale() as usize;
    let negative = value.mantissa() < 0;
    let digits = i128::from(value.mantissa()).abs().to_string();
    if scale == 0 {
        return format!("{}{digits}", if negative { "-" } else { "" });
    }
    let padded = format!("{:0>width$}", digits, width = scale + 1);
    let split = padded.len() - scale;
    format!(
        "{}{}.{}",
        if negative { "-" } else { "" },
        &padded[..split],
        &padded[split..]
    )
}

fn optional_decimal(value: Option<&Decimal64>) -> Option<String> {
    value.map(decimal_text)
}

fn enum_name(value: Option<&str>) -> String {
    value.unwrap_or("UNSPECIFIED").to_ascii_lowercase()
}
