use std::path::{Path, PathBuf};

use kairos_primitives::runtime::ActorId;

use crate::{ContractError, ContractResult};
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RiskViewKind {
    Latest,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RiskViewKey {
    pub actor_id: ActorId,
    pub kind: RiskViewKind,
}
impl RiskViewKey {
    pub fn latest(actor_id: impl Into<String>) -> Self {
        Self {
            actor_id: ActorId::new(actor_id.into()).expect("risk actor identity is validated"),
            kind: RiskViewKind::Latest,
        }
    }
    pub(crate) fn resource_path(&self, root: impl AsRef<Path>) -> ContractResult<PathBuf> {
        if self.actor_id.trim().is_empty() {
            return Err(ContractError::Invalid("Risk view actor id is empty".into()));
        }
        Ok(root
            .as_ref()
            .join("risk")
            .join(component(&self.actor_id))
            .join("latest/current.snapshot"))
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
