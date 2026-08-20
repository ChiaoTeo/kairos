use std::path::{Path, PathBuf};

use crate::{ContractError, ContractResult};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapitalViewKey {
    pub capital_group_id: String,
}

impl CapitalViewKey {
    pub fn current(capital_group_id: impl Into<String>) -> Self {
        Self {
            capital_group_id: capital_group_id.into(),
        }
    }

    pub fn canonical_key(&self) -> String {
        format!("capital.current/{}", component(&self.capital_group_id))
    }

    pub(crate) fn resource_path(&self, root: impl AsRef<Path>) -> ContractResult<PathBuf> {
        if self.capital_group_id.trim().is_empty() {
            return Err(ContractError::Invalid(
                "Capital view group id is empty".into(),
            ));
        }
        Ok(root
            .as_ref()
            .join("capital")
            .join(component(&self.capital_group_id))
            .join("current/current.snapshot"))
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
