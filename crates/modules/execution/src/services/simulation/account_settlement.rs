//! Simulation-only Account settlement delivery.
//!
//! Live Account observations come directly from Account-owned Integration
//! capabilities. This service exists only because a local simulator has no
//! exchange Account stream to produce the corresponding balance/position
//! mutation.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use kairos_account_contract::{AccountContractClient, SimulatedSettlement};
use kairos_primitives::SignedQuantity;
use rust_decimal::Decimal;
use serde_json::Value;

use crate::domain::{ExecutionFill, ExecutionOrder, OrderCommitment, OrderSide};

pub struct SimulatedAccountSettlement {
    accounts: BTreeMap<String, PathBuf>,
    clients: BTreeMap<String, AccountContractClient>,
}

impl SimulatedAccountSettlement {
    pub fn from_manifest(path: impl AsRef<Path>) -> Result<Self, String> {
        let value: Value = serde_json::from_slice(
            &std::fs::read(path.as_ref())
                .map_err(|error| format!("read endpoint manifest: {error}"))?,
        )
        .map_err(|error| format!("decode endpoint manifest: {error}"))?;
        let accounts = value
            .get("accounts")
            .and_then(Value::as_object)
            .ok_or_else(|| "endpoint manifest has no accounts".to_string())?
            .iter()
            .map(|(account_id, endpoint)| {
                let socket = endpoint
                    .get("socket")
                    .and_then(Value::as_str)
                    .ok_or_else(|| format!("account {account_id} has no socket"))?;
                Ok((account_id.clone(), PathBuf::from(socket)))
            })
            .collect::<Result<_, String>>()?;
        Ok(Self {
            accounts,
            clients: BTreeMap::new(),
        })
    }

    fn client(&mut self, account_id: &str) -> Result<&AccountContractClient, String> {
        if !self.clients.contains_key(account_id) {
            let socket = self
                .accounts
                .get(account_id)
                .ok_or_else(|| format!("account is not bound: {account_id}"))?;
            let client =
                AccountContractClient::connect(socket).map_err(|error| error.to_string())?;
            self.clients.insert(account_id.to_owned(), client);
        }
        self.clients
            .get(account_id)
            .ok_or_else(|| format!("account client is unavailable: {account_id}"))
    }

    pub(crate) fn apply_fill(
        &mut self,
        fill: &ExecutionFill,
        order: &ExecutionOrder,
        commitment: &OrderCommitment,
    ) -> Result<(), String> {
        if order.order_id != fill.order_id {
            return Err("fill/order identity mismatch".into());
        }
        let notional = Decimal::try_new(fill.quantity.mantissa(), fill.quantity.scale().into())
            .ok()
            .and_then(|quantity| {
                Decimal::try_new(fill.price.mantissa(), fill.price.scale().into())
                    .ok()
                    .and_then(|price| quantity.checked_mul(price))
            })
            .ok_or_else(|| "simulated settlement notional overflow".to_string())?;
        let settlement_delta = if fill.side == OrderSide::Buy {
            -notional
        } else {
            notional
        }
        .normalize();
        let account_id = order.account_id.to_string();
        self.client(&account_id)?
            .apply_simulated_settlement(&SimulatedSettlement {
                fill_id: fill.fill_id.clone(),
                order_id: Some(fill.order_id.clone()),
                segment_key: order.segment_key.clone(),
                instrument_id: fill.instrument_id.clone(),
                quantity: fill.quantity,
                price: fill.price,
                side: fill.side,
                settlement_asset: Some(
                    commitment
                        .settlement_asset
                        .as_ref()
                        .ok_or_else(|| {
                            "simulated fill has no Reference-confirmed settlement asset".to_string()
                        })?
                        .clone(),
                ),
                settlement_delta: Some(
                    SignedQuantity::new(
                        i64::try_from(settlement_delta.mantissa())
                            .map_err(|_| "settlement delta exceeds Decimal64 range")?,
                        settlement_delta.scale() as u8,
                    )
                    .map_err(|error| error.to_string())?,
                ),
                fee_asset: fill.fee_currency.clone(),
                fee_amount: (fill.fee.mantissa() != 0).then_some(
                    SignedQuantity::new(fill.fee.mantissa(), fill.fee.scale())
                        .map_err(|error| error.to_string())?,
                ),
                occurred_at_unix_nanos: fill.occurred_at_unix_nanos,
            })
            .map_err(|error| error.to_string())
    }
}
