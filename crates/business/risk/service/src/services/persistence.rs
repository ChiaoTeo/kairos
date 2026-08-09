use crate::application::RiskSnapshot;
use std::path::PathBuf;

/// Internal persistence capability for the Risk actor.
///
/// Persistence is an implementation detail and is not part of the public Risk
/// use-case API.
pub(crate) trait RiskStateStore: Send {
    fn load(&mut self) -> Result<Option<RiskSnapshot>, String>;
    fn save(&mut self, snapshot: &RiskSnapshot) -> Result<(), String>;
}

#[derive(Default)]
/// Durable state for one Risk runtime. The launch composition supplies the
/// path; Risk does not know whether its context came from a shared or local
/// deployment.
pub(crate) struct JsonRiskStore {
    pub path: PathBuf,
}

impl JsonRiskStore {
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self { path: path.into() }
    }
}

impl RiskStateStore for JsonRiskStore {
    fn load(&mut self) -> Result<Option<RiskSnapshot>, String> {
        if !self.path.exists() {
            return Ok(None);
        }
        let bytes = std::fs::read(&self.path).map_err(|error| error.to_string())?;
        serde_json::from_slice(&bytes).map_err(|error| error.to_string())
    }

    fn save(&mut self, snapshot: &RiskSnapshot) -> Result<(), String> {
        if let Some(parent) = self.path.parent() {
            std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        let bytes = serde_json::to_vec_pretty(snapshot).map_err(|error| error.to_string())?;
        let temporary = self.path.with_extension("tmp");
        std::fs::write(&temporary, bytes).map_err(|error| error.to_string())?;
        std::fs::rename(temporary, &self.path).map_err(|error| error.to_string())
    }
}
