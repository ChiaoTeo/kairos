use std::path::PathBuf;

#[derive(Clone, Debug, serde::Deserialize)]
pub(super) struct ReplayManifest {
    pub(super) path: PathBuf,
    pub(super) event_count: usize,
}
