use std::path::{Path, PathBuf};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ReferenceViewKind {
    Latest,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReferenceViewKey {
    pub actor_id: String,
    pub kind: ReferenceViewKind,
}

impl ReferenceViewKey {
    pub fn latest(actor_id: impl Into<String>) -> Self {
        Self {
            actor_id: actor_id.into(),
            kind: ReferenceViewKind::Latest,
        }
    }
    pub fn resource_path(&self, root: impl AsRef<Path>) -> PathBuf {
        root.as_ref()
            .join("reference")
            .join(&self.actor_id)
            .join("latest/current.snapshot")
    }
}
