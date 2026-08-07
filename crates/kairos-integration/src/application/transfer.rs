//! Provider-neutral account transfer capability.

use crate::application::connection::Connection;
use crate::domain::account::{
    ExternalAccountSegment as AccountSegment, ExternalDecimal as DecimalValue,
};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TransferRequest {
    pub source: AccountSegment,
    pub destination: AccountSegment,
    pub asset: String,
    pub amount: DecimalValue,
}

impl TransferRequest {
    pub fn validate(&self) -> Result<(), String> {
        if self.asset.trim().is_empty() {
            return Err("transfer asset is required".into());
        }
        if self.amount.mantissa <= 0 {
            return Err("transfer amount must be positive".into());
        }
        if self.source.identity != self.destination.identity {
            return Err("source and destination must belong to the same external account".into());
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TransferResult {
    pub accepted: bool,
    pub reference_id: Option<String>,
    pub reason: String,
}

pub trait TransferConnection: Connection {
    fn transfer(&mut self, request: &TransferRequest) -> Result<TransferResult, String>;
}
