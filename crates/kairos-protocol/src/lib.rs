//! Generated process-boundary contracts.

pub mod generated;

/// Runtime ownership identity carried by instance-scoped transport headers.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct InstanceIdentity {
    pub workspace_id: String,
    pub launch_id: String,
    pub instance_id: String,
}

impl InstanceIdentity {
    pub fn new(
        workspace_id: impl Into<String>,
        launch_id: impl Into<String>,
        instance_id: impl Into<String>,
    ) -> Self {
        Self {
            workspace_id: workspace_id.into(),
            launch_id: launch_id.into(),
            instance_id: instance_id.into(),
        }
    }

    pub fn is_instance_scoped(&self) -> bool {
        !self.launch_id.is_empty() && !self.instance_id.is_empty()
    }
}
