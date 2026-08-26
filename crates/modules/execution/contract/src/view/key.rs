use std::path::{Path, PathBuf};

use crate::{ContractError, ContractResult};

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum ExecutionViewKind {
    CurrentExecution,
}
impl ExecutionViewKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::CurrentExecution => "current-execution",
        }
    }
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct ExecutionViewKey {
    pub workspace_id: kairos_primitives::runtime::WorkspaceId,
    pub launch_id: Option<kairos_primitives::runtime::LaunchId>,
    pub instance_id: Option<kairos_primitives::runtime::InstanceId>,
    pub kind: ExecutionViewKind,
}
impl ExecutionViewKey {
    pub fn new(
        workspace_id: impl Into<String>,
        kind: ExecutionViewKind,
        launch_id: Option<impl Into<String>>,
        instance_id: Option<impl Into<String>>,
    ) -> ContractResult<Self> {
        let workspace_id = kairos_primitives::runtime::WorkspaceId::new(workspace_id)
            .map_err(|error| ContractError::Invalid(error.to_string()))?;
        let launch_id = launch_id
            .map(|value| kairos_primitives::runtime::LaunchId::new(value))
            .transpose()
            .map_err(|error| ContractError::Invalid(error.to_string()))?;
        let instance_id = instance_id
            .map(|value| kairos_primitives::runtime::InstanceId::new(value))
            .transpose()
            .map_err(|error| ContractError::Invalid(error.to_string()))?;
        Ok(Self {
            workspace_id,
            launch_id,
            instance_id,
            kind,
        })
    }

    pub fn from_identity(
        identity: &kairos_primitives::runtime::InstanceIdentity,
        kind: ExecutionViewKind,
    ) -> Self {
        Self {
            workspace_id: identity.workspace_id.clone(),
            launch_id: identity.launch_id().cloned(),
            instance_id: identity.instance_id().cloned(),
            kind,
        }
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
            .join(format!(
                "launch={}",
                self.launch_id
                    .as_deref()
                    .map(component)
                    .unwrap_or_else(|| "_".to_owned())
            ))
            .join(format!(
                "instance={}",
                self.instance_id
                    .as_deref()
                    .map(component)
                    .unwrap_or_else(|| "_".to_owned())
            ))
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
