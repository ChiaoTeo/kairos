//! Account connected/runtime application facade.
//!
//! Connected CLI entry points use this facade to query Account runtime
//! projections or call typed Account runtime control. Standalone Account CLI
//! commands must use `CliAccountApplication`.

use std::path::PathBuf;

use kairos_account_contract::{
    AccountClient, AccountControlRpcClient, AccountSegmentsRequest, SimulatedSettlement,
};
use kairos_protocol::generated::kairos::common::v_2::{Decimal64, ViewCompleteness};
use serde_json::Value;

pub struct ConnectedAccountApplication {
    client: AccountClient,
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
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let status =
            AccountControlRpcClient::apply_simulated_settlement(&self.client.control(), settlement)
                .await?;
        Ok(serde_json::to_value(status)?)
    }

    pub async fn refresh(
        &self,
        request: AccountSegmentsRequest,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let response = AccountControlRpcClient::refresh(&self.client.control(), request).await?;
        Ok(serde_json::to_value(response)?)
    }

    pub async fn reconcile(
        &self,
        request: AccountSegmentsRequest,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let response = AccountControlRpcClient::reconcile(&self.client.control(), request).await?;
        Ok(serde_json::to_value(response)?)
    }

    pub fn snapshot(
        &self,
        account_id: &str,
        symbol: Option<&str>,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        self.current(account_id, &[], symbol, false, None)
    }

    pub fn balances(
        &self,
        account_id: &str,
        segments: &[String],
        include_zero: bool,
        page: usize,
        page_size: usize,
    ) -> Result<Value, Box<dyn std::error::Error>> {
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
    ) -> Result<Value, Box<dyn std::error::Error>> {
        self.current(account_id, segments, symbol, false, None)
    }

    pub fn observed_orders(
        &self,
        account_id: &str,
        symbol: Option<&str>,
        limit: Option<usize>,
    ) -> Result<Value, Box<dyn std::error::Error>> {
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
                orders.push(serde_json::json!({
                    "segment_key": segment.segment_key(),
                    "observation_id": order.observation_id(),
                    "source_id": order.source_id(),
                    "execution_order_id": order.execution_order_id(),
                    "remote_order_id": order.remote_order_id(),
                    "instrument_id": order.instrument_id(),
                    "market_id": order.market_id(),
                    "side": order.side().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
                    "quantity": decimal_text(order.quantity()),
                    "filled_quantity": decimal_text(order.filled_quantity()),
                    "status": order.status().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
                    "observed_at_unix_nanos": order.observed_at_unix_nanos(),
                }));
                if limit.is_some_and(|limit| orders.len() >= limit) {
                    break;
                }
            }
            if limit.is_some_and(|limit| orders.len() >= limit) {
                break;
            }
        }
        Ok(serde_json::json!({
            "account_id": account_id,
            "generation": frame.generation(),
            "orders": orders,
        }))
    }

    fn current(
        &self,
        account_id: &str,
        segment_filter: &[String],
        symbol_filter: Option<&str>,
        include_zero: bool,
        balance_page: Option<(usize, usize)>,
    ) -> Result<Value, Box<dyn std::error::Error>> {
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
                .map(|balance| {
                    serde_json::json!({
                        "asset_id": balance.asset_id(),
                        "asset_code": balance.asset_code(),
                        "total": decimal_text(balance.total()),
                        "available": optional_decimal(balance.available()),
                        "locked": optional_decimal(balance.locked()),
                        "borrowed": optional_decimal(balance.borrowed()),
                        "interest": optional_decimal(balance.interest()),
                    })
                })
                .collect::<Vec<_>>();
            let collateral = segment
                .collateral()
                .iter()
                .filter(|balance| include_zero || balance.total().mantissa() != 0)
                .map(|balance| {
                    serde_json::json!({
                        "asset_id": balance.asset_id(),
                        "asset_code": balance.asset_code(),
                        "total": decimal_text(balance.total()),
                        "available": optional_decimal(balance.available()),
                        "locked": optional_decimal(balance.locked()),
                        "borrowed": optional_decimal(balance.borrowed()),
                        "interest": optional_decimal(balance.interest()),
                    })
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
                .map(|position| {
                    serde_json::json!({
                        "instrument_id": position.instrument_id(),
                        "market_id": position.market_id(),
                        "quantity": decimal_text(position.quantity()),
                        "average_price": optional_decimal(position.average_price()),
                        "mark_price": optional_decimal(position.mark_price()),
                        "unrealized_pnl": optional_decimal(position.unrealized_pnl()),
                        "realized_pnl": optional_decimal(position.realized_pnl()),
                        "observed_at_unix_nanos": position.observed_at_unix_nanos(),
                    })
                })
                .collect::<Vec<_>>();
            let earn_holdings = segment
                .earn_holdings()
                .iter()
                .map(|holding| {
                    serde_json::json!({
                        "holding_key": holding.holding_key(),
                        "participant_position_id": holding.participant_position_id(),
                        "product_id": holding.product_id(),
                        "asset": holding.asset(),
                        "principal": decimal_text(holding.principal()),
                        "redeemable": optional_decimal(holding.redeemable()),
                        "state": holding.state().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
                        "participant_state": holding.participant_state(),
                        "liquidity": holding.liquidity().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
                        "notice_seconds": holding.notice_seconds(),
                        "matures_at_unix_nanos": holding.matures_at_unix_nanos(),
                        "observed_at_unix_nanos": holding.observed_at_unix_nanos(),
                    })
                })
                .collect::<Vec<_>>();
            segments.push(serde_json::json!({
                "segment_key": segment.segment_key(),
                "environment": segment.environment(),
                "broker": segment.broker(),
                "configured_account_model": segment.configured_account_model().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
                "observed_account_model": segment.observed_account_model().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
                "margin_mode": segment.margin_mode().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
                "position_mode": segment.position_mode().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
                "status": segment.status().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
                "freshness": segment.freshness().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
                "sync_mode": segment.sync_mode().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
                "sync_lifecycle": segment.sync_lifecycle().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
                "completeness": segment.completeness().variant_name().unwrap_or("UNSPECIFIED").to_ascii_lowercase(),
                "snapshot_watermark": segment.snapshot_watermark(),
                "event_watermark": segment.event_watermark(),
                "channel_epoch": segment.channel_epoch(),
                "last_event_at_unix_nanos": segment.last_event_at_unix_nanos(),
                "last_success_at_unix_nanos": segment.last_success_at_unix_nanos(),
                "last_error": segment.last_error(),
                "recovery_buffer_depth": segment.recovery_buffer_depth(),
                "observed_at_unix_nanos": segment.observed_at_unix_nanos(),
                "state_generation": segment.state_generation(),
                "balances": balances,
                "collateral": collateral,
                "positions": positions,
                "earn_holdings": earn_holdings,
                "earn_watermark_unix_nanos": segment.earn_watermark_unix_nanos(),
            }));
        }
        let mut result = serde_json::json!({
            "account_id": account_id,
            "generation": frame.generation(),
            "event_sequence": frame.envelope_metadata().applied_event_sequence,
            "producer_incarnation": frame.envelope_metadata().producer_incarnation,
            "segments": segments,
        });
        if let Some((page, page_size)) = balance_page {
            result["page"] = page.into();
            result["page_size"] = page_size.into();
        }
        Ok(result)
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

fn optional_decimal(value: Option<&Decimal64>) -> Value {
    value
        .map(decimal_text)
        .map(Value::String)
        .unwrap_or(Value::Null)
}
