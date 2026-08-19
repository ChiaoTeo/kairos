//! Runtime identities shared by process contracts and protocol adapters.
//!
//! These are validated semantic values, not transport messages.  Keeping them
//! here lets protocol code consume the same identity types as module contracts
//! without defining a second, incompatible copy.

use serde::{Deserialize, Serialize};

macro_rules! runtime_id {
    ($name:ident) => {
        #[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
        #[serde(transparent)]
        pub struct $name(String);

        impl $name {
            pub fn new(value: impl Into<String>) -> Result<Self, crate::DomainTypeError> {
                let value = value.into();
                if value.is_empty() {
                    return Err(crate::DomainTypeError::Empty {
                        type_name: stringify!($name),
                    });
                }
                if value.trim() != value {
                    return Err(crate::DomainTypeError::Whitespace {
                        type_name: stringify!($name),
                    });
                }
                Ok(Self(value))
            }

            pub fn as_str(&self) -> &str {
                &self.0
            }
        }

        impl<'de> Deserialize<'de> for $name {
            fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
            where
                D: serde::Deserializer<'de>,
            {
                let value = <String as Deserialize>::deserialize(deserializer)?;
                Self::new(value).map_err(serde::de::Error::custom)
            }
        }

        impl AsRef<str> for $name {
            fn as_ref(&self) -> &str {
                self.as_str()
            }
        }

        impl std::ops::Deref for $name {
            type Target = str;
            fn deref(&self) -> &Self::Target {
                self.as_str()
            }
        }

        impl std::fmt::Display for $name {
            fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                formatter.write_str(self.as_str())
            }
        }
    };
}

runtime_id!(WorkspaceId);
runtime_id!(LaunchId);
runtime_id!(InstanceId);
runtime_id!(ActorId);
runtime_id!(ProducerId);
runtime_id!(EventId);

/// Runtime ownership identity carried by instance-scoped transport headers.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct InstanceIdentity {
    pub workspace_id: WorkspaceId,
    pub launch_id: LaunchId,
    pub instance_id: InstanceId,
    instance_scoped: bool,
}

impl InstanceIdentity {
    pub fn new(
        workspace_id: impl Into<String>,
        launch_id: impl Into<String>,
        instance_id: impl Into<String>,
    ) -> Result<Self, crate::DomainTypeError> {
        Ok(Self {
            workspace_id: WorkspaceId::new(workspace_id)?,
            launch_id: LaunchId::new(launch_id)?,
            instance_id: InstanceId::new(instance_id)?,
            instance_scoped: true,
        })
    }

    pub fn unscoped(workspace_id: impl Into<String>) -> Result<Self, crate::DomainTypeError> {
        Ok(Self {
            workspace_id: WorkspaceId::new(workspace_id)?,
            launch_id: LaunchId::new("runtime:unscoped")?,
            instance_id: InstanceId::new("runtime:unscoped")?,
            instance_scoped: false,
        })
    }

    pub fn is_instance_scoped(&self) -> bool {
        self.instance_scoped
    }

    pub fn launch_id(&self) -> Option<&LaunchId> {
        self.instance_scoped.then_some(&self.launch_id)
    }

    pub fn instance_id(&self) -> Option<&InstanceId> {
        self.instance_scoped.then_some(&self.instance_id)
    }
}

impl Default for InstanceIdentity {
    fn default() -> Self {
        Self::unscoped("workspace:unscoped").expect("canonical unscoped runtime identity is valid")
    }
}
