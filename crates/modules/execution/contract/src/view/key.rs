use crate::{ContractError, ContractResult};
use std::path::{Path, PathBuf};

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum ExecutionViewKind {
    ActiveOrders,
    ActiveIntents,
    CurrentExecution,
}
impl ExecutionViewKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::ActiveOrders => "active-orders",
            Self::ActiveIntents => "active-intents",
            Self::CurrentExecution => "current-execution",
        }
    }
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct ExecutionViewKey {
    pub workspace_id: String,
    pub launch_id: Option<String>,
    pub instance_id: Option<String>,
    pub kind: ExecutionViewKind,
}
impl ExecutionViewKey {
    pub fn new(
        workspace_id: impl Into<String>,
        kind: ExecutionViewKind,
        launch_id: Option<impl Into<String>>,
        instance_id: Option<impl Into<String>>,
    ) -> ContractResult<Self> {
        let workspace_id = workspace_id.into();
        if workspace_id.trim().is_empty() {
            return Err(ContractError::Invalid(
                "view workspace identity is incomplete".into(),
            ));
        }
        Ok(Self {
            workspace_id,
            launch_id: launch_id.map(Into::into),
            instance_id: instance_id.map(Into::into),
            kind,
        })
    }
    pub fn canonical_key(&self) -> String {
        format!(
            "workspace={};launch={};instance={};view={}",
            self.workspace_id,
            self.launch_id.as_deref().unwrap_or(""),
            self.instance_id.as_deref().unwrap_or(""),
            self.kind.as_str()
        )
    }
    pub(crate) fn resource_path(&self, root: impl AsRef<Path>) -> PathBuf {
        root.as_ref()
            .join("execution")
            .join("views")
            .join(component(&self.workspace_id))
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
