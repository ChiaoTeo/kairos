use std::path::{Path, PathBuf};
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RiskViewKind {
    Latest,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RiskViewKey {
    pub actor_id: String,
    pub kind: RiskViewKind,
}
impl RiskViewKey {
    pub fn latest(actor_id: impl Into<String>) -> Self {
        Self {
            actor_id: actor_id.into(),
            kind: RiskViewKind::Latest,
        }
    }
    pub fn resource_path(&self, root: impl AsRef<Path>) -> PathBuf {
        root.as_ref()
            .join("risk")
            .join(&self.actor_id)
            .join("latest/current.snapshot")
    }
}
