use std::path::{Path, PathBuf};

use crate::{ContractError, ContractResult};

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum AccountViewKind {
    Current,
    ObservedOrders,
}
impl AccountViewKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Current => "current",
            Self::ObservedOrders => "observed-orders",
        }
    }
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct AccountViewKey {
    pub account_runtime_id: String,
    pub account_id: String,
    pub kind: AccountViewKind,
}
impl AccountViewKey {
    pub fn new(
        account_runtime_id: impl Into<String>,
        account_id: impl Into<String>,
        kind: AccountViewKind,
    ) -> ContractResult<Self> {
        let account_runtime_id = account_runtime_id.into();
        let account_id = account_id.into();
        if account_runtime_id.trim().is_empty() || account_id.trim().is_empty() {
            return Err(ContractError::Invalid("view identity is incomplete".into()));
        }
        Ok(Self {
            account_runtime_id,
            account_id,
            kind,
        })
    }
    pub fn canonical_key(&self) -> String {
        format!(
            "runtime={};account={};view={}",
            self.account_runtime_id,
            self.account_id,
            self.kind.as_str()
        )
    }
    pub(crate) fn resource_path(&self, root: impl AsRef<Path>) -> PathBuf {
        root.as_ref()
            .join("account")
            .join("views")
            .join(component(&self.account_runtime_id))
            .join(component(&self.account_id))
            .join(self.kind.as_str())
            .join("current.snapshot")
    }
}
fn component(value: &str) -> String {
    value
        .as_bytes()
        .iter()
        .map(|byte| {
            if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.') {
                format!("{}", *byte as char)
            } else {
                format!("%{byte:02X}")
            }
        })
        .collect()
}
