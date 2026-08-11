//! Shared workspace identity and layout validation for Rust processes.

use std::{
    collections::BTreeMap,
    fs::{self, File, OpenOptions},
    io,
    path::{Path, PathBuf},
};

#[cfg(unix)]
use std::os::fd::AsRawFd;

use serde::Deserialize;
use sha2::{Digest, Sha256};

fn default_cli_format() -> String {
    "json".into()
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub struct WorkspaceCliConfig {
    #[serde(default = "default_cli_format")]
    pub format: String,
}

impl Default for WorkspaceCliConfig {
    fn default() -> Self {
        Self {
            format: default_cli_format(),
        }
    }
}

#[derive(Debug, Clone, Default, Deserialize, PartialEq, Eq)]
pub struct WorkspaceMarketConfig {
    /// Provider-native source bindings. The map key is a stable Market
    /// SourceId and is deliberately independent from provider vocabulary.
    #[serde(default)]
    pub sources: BTreeMap<String, WorkspaceMarketSourceBinding>,
    /// Named runtime policies select source ids; they never repeat provider
    /// connection details.
    #[serde(default)]
    pub profiles: BTreeMap<String, WorkspaceMarketRuntimeProfile>,
    pub default_profile: Option<String>,
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
#[serde(tag = "type", rename_all = "kebab-case")]
pub enum WorkspaceMarketSourceBinding {
    BinanceSpot {
        #[serde(default = "enabled_by_default")]
        enabled: bool,
        #[serde(default)]
        transport: WorkspaceBinanceSpotTransport,
        endpoint: Option<String>,
        #[serde(default = "default_market_source_snapshot_interval_ms")]
        snapshot_interval_ms: u64,
    },
    BinanceDerivatives {
        #[serde(default = "enabled_by_default")]
        enabled: bool,
        product: WorkspaceBinanceDerivativeProduct,
        #[serde(default)]
        transport: WorkspaceBinanceDerivativeTransport,
        endpoint: Option<String>,
        #[serde(default = "default_market_source_snapshot_interval_ms")]
        snapshot_interval_ms: u64,
    },
    Massive {
        #[serde(default = "enabled_by_default")]
        enabled: bool,
        product: WorkspaceMassiveMarketProduct,
        exchange: String,
        credential_id: String,
        endpoint: Option<String>,
    },
    Okx {
        #[serde(default = "enabled_by_default")]
        enabled: bool,
        instrument_type: WorkspaceOkxInstrumentType,
        #[serde(default)]
        transport: WorkspacePublicMarketTransport,
        endpoint: Option<String>,
        #[serde(default = "default_market_source_snapshot_interval_ms")]
        snapshot_interval_ms: u64,
    },
    Hyperliquid {
        #[serde(default = "enabled_by_default")]
        enabled: bool,
        market_type: WorkspaceHyperliquidMarketType,
        #[serde(default)]
        transport: WorkspacePublicMarketTransport,
        endpoint: Option<String>,
        #[serde(default = "default_market_source_snapshot_interval_ms")]
        snapshot_interval_ms: u64,
    },
}

impl WorkspaceMarketSourceBinding {
    pub fn enabled(&self) -> bool {
        match self {
            Self::BinanceSpot { enabled, .. }
            | Self::BinanceDerivatives { enabled, .. }
            | Self::Massive { enabled, .. }
            | Self::Okx { enabled, .. }
            | Self::Hyperliquid { enabled, .. } => *enabled,
        }
    }
}

fn enabled_by_default() -> bool {
    true
}

#[derive(Debug, Clone, Copy, Default, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum WorkspaceBinanceSpotTransport {
    Rest,
    #[default]
    Websocket,
}

#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum WorkspaceBinanceDerivativeProduct {
    UsdMFutures,
    CoinMFutures,
    Options,
}

#[derive(Debug, Clone, Copy, Default, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum WorkspaceBinanceDerivativeTransport {
    #[default]
    Rest,
    Websocket,
}

#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum WorkspaceMassiveMarketProduct {
    Equity,
    Options,
}

#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum WorkspaceOkxInstrumentType {
    Spot,
    Swap,
    Futures,
    Options,
}

#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum WorkspaceHyperliquidMarketType {
    Spot,
    Perpetual,
}

/// Public market delivery mode. Live WebSocket is the production default;
/// REST snapshots remain available for diagnostics and constrained venues.
#[derive(Debug, Clone, Copy, Default, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum WorkspacePublicMarketTransport {
    Rest,
    #[default]
    Websocket,
}

#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum WorkspaceMarketRuntimeScope {
    Shared,
    Instance,
    Replay,
    Diagnostic,
}

#[derive(Debug, Clone, Copy, Default, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum WorkspaceMarketReplayClock {
    #[default]
    Maximum,
    EventTime,
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub struct WorkspaceMarketReplayConfig {
    #[serde(default)]
    pub start_unix_nanos: Option<u64>,
    #[serde(default)]
    pub end_unix_nanos: Option<u64>,
    #[serde(default)]
    pub clock: WorkspaceMarketReplayClock,
    #[serde(default = "default_market_replay_speed_multiplier")]
    pub speed_multiplier: u32,
    #[serde(default)]
    pub start_paused: bool,
}

impl Default for WorkspaceMarketReplayConfig {
    fn default() -> Self {
        Self {
            start_unix_nanos: None,
            end_unix_nanos: None,
            clock: WorkspaceMarketReplayClock::Maximum,
            speed_multiplier: default_market_replay_speed_multiplier(),
            start_paused: false,
        }
    }
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub struct WorkspaceMarketRuntimeProfile {
    pub scope: WorkspaceMarketRuntimeScope,
    #[serde(default = "default_market_source_input_capacity")]
    pub source_input_capacity: usize,
    #[serde(default = "default_market_publication_queue_capacity")]
    pub publication_queue_capacity: usize,
    #[serde(default = "default_market_snapshot_interval_ms")]
    pub snapshot_interval_ms: u64,
    #[serde(default = "default_market_freshness_check_interval_ms")]
    pub freshness_check_interval_ms: u64,
    #[serde(default = "default_market_freshness_max_age_ms")]
    pub freshness_max_age_ms: u64,
    #[serde(default = "default_market_reference_recovery_interval_ms")]
    pub reference_recovery_interval_ms: u64,
    #[serde(default = "default_market_shutdown_timeout_ms")]
    pub shutdown_timeout_ms: u64,
    #[serde(default)]
    pub replay: Option<WorkspaceMarketReplayConfig>,
}

fn default_market_replay_speed_multiplier() -> u32 {
    1
}

fn default_market_source_input_capacity() -> usize {
    10_000
}

fn default_market_publication_queue_capacity() -> usize {
    256
}

fn default_market_snapshot_interval_ms() -> u64 {
    1_000
}

fn default_market_source_snapshot_interval_ms() -> u64 {
    1_000
}

fn default_market_freshness_check_interval_ms() -> u64 {
    250
}

fn default_market_freshness_max_age_ms() -> u64 {
    5_000
}

fn default_market_reference_recovery_interval_ms() -> u64 {
    500
}

fn default_market_shutdown_timeout_ms() -> u64 {
    5_000
}

#[derive(Debug, Clone, Default, Deserialize, PartialEq, Eq)]
pub struct WorkspaceReferenceProviderConfig {
    pub enabled: Option<bool>,
    pub credential_id: Option<String>,
    pub endpoint: Option<String>,
}

#[derive(Debug, Clone, Default, Deserialize, PartialEq, Eq)]
pub struct WorkspaceReferenceProductConfig {
    pub enabled: Option<bool>,
    pub credential_id: Option<String>,
    pub endpoint: Option<String>,
}

#[derive(Debug, Clone, Default, Deserialize, PartialEq, Eq)]
pub struct WorkspaceReferenceParticipantConfig {
    #[serde(rename = "type")]
    pub entity_type: String,
    pub name: String,
    pub enabled: Option<bool>,
}

#[derive(Debug, Clone, Default, Deserialize, PartialEq, Eq)]
pub struct WorkspaceReferenceConfig {
    #[serde(default)]
    pub providers: BTreeMap<String, WorkspaceReferenceProviderConfig>,
    #[serde(default)]
    pub products: BTreeMap<String, BTreeMap<String, WorkspaceReferenceProductConfig>>,
    #[serde(default)]
    pub participants: BTreeMap<String, WorkspaceReferenceParticipantConfig>,
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub struct WorkspaceManifest {
    pub version: u32,
    pub workspace_id: String,
    #[serde(default)]
    pub cli: WorkspaceCliConfig,
    #[serde(default)]
    pub market: WorkspaceMarketConfig,
    #[serde(default)]
    pub reference: WorkspaceReferenceConfig,
}

#[derive(Debug, Clone)]
pub struct Workspace {
    root: PathBuf,
    manifest: WorkspaceManifest,
}

/// A workspace-scoped process lock held for the lifetime of its file handle.
///
/// The lock file is intentionally retained on disk. The advisory OS lock,
/// rather than stale PID or socket contents, is the source of truth; the OS
/// releases it automatically if the owner exits unexpectedly.
#[derive(Debug)]
pub struct WorkspaceProcessLock {
    _file: File,
    path: PathBuf,
}

impl WorkspaceProcessLock {
    pub fn path(&self) -> &Path {
        &self.path
    }
}

#[derive(Debug, Clone)]
pub struct InstanceWorkspace {
    workspace: Workspace,
    mode: String,
    launch_id: String,
    instance_id: String,
}

impl InstanceWorkspace {
    fn component(name: &str) -> io::Result<&str> {
        if name.trim().is_empty()
            || name == "."
            || name == ".."
            || name.contains('/')
            || name.contains('\\')
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid instance resource name",
            ));
        }
        Ok(name)
    }

    fn components<'a>(parts: &'a [&'a str]) -> io::Result<&'a [&'a str]> {
        for part in parts {
            Self::component(part)?;
        }
        Ok(parts)
    }

    fn new(
        workspace: &Workspace,
        mode: impl Into<String>,
        launch_id: impl Into<String>,
        instance_id: impl Into<String>,
    ) -> io::Result<Self> {
        let mode = mode.into();
        let launch_id = launch_id.into();
        let instance_id = instance_id.into();
        for (name, value) in [
            ("mode", &mode),
            ("launch id", &launch_id),
            ("instance id", &instance_id),
        ] {
            if value.trim().is_empty()
                || value == "."
                || value == ".."
                || value.contains('/')
                || value.contains('\\')
            {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    format!("invalid {name}"),
                ));
            }
        }
        Ok(Self {
            workspace: workspace.clone(),
            mode,
            launch_id,
            instance_id,
        })
    }

    pub fn root(&self) -> PathBuf {
        self.workspace
            .root
            .join("launches")
            .join(&self.mode)
            .join(&self.launch_id)
            .join("instances")
            .join(&self.instance_id)
    }

    pub fn socket(&self, name: &str) -> io::Result<PathBuf> {
        let name = Self::component(name)?;
        let candidate = self.root().join("sockets").join(format!("{name}.sock"));
        if candidate.to_string_lossy().len() <= 100 {
            return Ok(candidate);
        }
        let input = format!(
            "{}:{}:{}:{}:{}",
            self.workspace.root.display(),
            self.mode,
            self.launch_id,
            self.instance_id,
            name
        );
        let digest = Sha256::digest(input.as_bytes());
        let short = digest[..10]
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>();
        Ok(PathBuf::from(format!(
            "/tmp/kairos-instance-{short}-{name}.sock"
        )))
    }

    pub fn health(&self, name: &str) -> io::Result<PathBuf> {
        Ok(self
            .root()
            .join("health")
            .join(format!("{}.json", Self::component(name)?)))
    }

    pub fn process_lock(&self, name: &str) -> io::Result<WorkspaceProcessLock> {
        let name = Self::component(name)?;
        let path = self.root().join("locks").join(format!("{}.lock", name));
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let file = OpenOptions::new()
            .create(true)
            .read(true)
            .write(true)
            .truncate(false)
            .open(&path)?;
        #[cfg(unix)]
        {
            let result = unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) };
            if result != 0 {
                let error = io::Error::last_os_error();
                if matches!(error.raw_os_error(), Some(code) if code == libc::EAGAIN || code == libc::EWOULDBLOCK)
                {
                    return Err(io::Error::new(
                        io::ErrorKind::AlreadyExists,
                        format!("process lock is already held: {}", path.display()),
                    ));
                }
                return Err(error);
            }
        }
        let _ = std::fs::write(&path, std::process::id().to_string());
        Ok(WorkspaceProcessLock { _file: file, path })
    }

    pub fn component_manifest(&self) -> io::Result<PathBuf> {
        self.state(&["component-endpoints.json"])
    }

    pub fn service_health(&self, service: &str) -> io::Result<PathBuf> {
        self.health(service)
    }

    pub fn state(&self, parts: &[&str]) -> io::Result<PathBuf> {
        Ok(self
            .root()
            .join("state")
            .join(Self::components(parts)?.iter().collect::<PathBuf>()))
    }

    pub fn snapshot(&self, parts: &[&str]) -> io::Result<PathBuf> {
        Ok(self
            .root()
            .join("snapshots")
            .join(Self::components(parts)?.iter().collect::<PathBuf>()))
    }

    pub fn service_snapshot(&self, service: &str) -> io::Result<PathBuf> {
        let service = Self::component(service)?;
        self.snapshot(&[service, &format!("{service}.snapshot")])
    }

    pub fn market_state(&self, name: &str) -> io::Result<PathBuf> {
        Ok(self.root().join("market").join(Self::component(name)?))
    }

    pub fn mode(&self) -> &str {
        &self.mode
    }
    pub fn launch_id(&self) -> &str {
        &self.launch_id
    }
    pub fn instance_id(&self) -> &str {
        &self.instance_id
    }

    pub fn prepare(&self) -> io::Result<()> {
        for directory in [
            self.root(),
            self.root().join("sockets"),
            self.root().join("health"),
            self.root().join("state"),
            self.root().join("snapshots"),
            self.root().join("logs"),
            self.root().join("checkpoints"),
            self.root().join("locks"),
            self.root().join("market"),
        ] {
            fs::create_dir_all(directory)?;
        }
        Ok(())
    }
}

impl Workspace {
    /// Acquires a workspace-wide lease for an external resource that permits
    /// only one owning process. The provider identity is hashed so endpoints
    /// and account-like identifiers are not exposed in lock filenames.
    pub fn exclusive_process_lock(
        &self,
        namespace: &str,
        provider_identity: &str,
    ) -> io::Result<WorkspaceProcessLock> {
        if namespace.trim().is_empty()
            || !namespace
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "exclusive process lock namespace is invalid",
            ));
        }
        if provider_identity.trim().is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "exclusive process lock identity is required",
            ));
        }
        let digest = Sha256::digest(provider_identity.as_bytes());
        let short = digest[..10]
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>();
        self.process_lock(&format!("exclusive-{namespace}-{short}"))
    }

    pub fn init_project(
        project_root: impl Into<PathBuf>,
        workspace_id: impl Into<String>,
    ) -> io::Result<Self> {
        let project_root = project_root.into();
        let storage = project_root.join(".kairos");
        fs::create_dir_all(&storage)?;
        Self::init_with_manifest(storage, "kairos.toml", workspace_id)?;
        Self::open(project_root)
    }

    pub fn init(root: impl Into<PathBuf>, workspace_id: impl Into<String>) -> io::Result<Self> {
        Self::init_with_manifest(root.into(), "workspace.toml", workspace_id)
    }

    fn init_with_manifest(
        root: PathBuf,
        manifest_name: &str,
        workspace_id: impl Into<String>,
    ) -> io::Result<Self> {
        fs::create_dir_all(&root)?;
        let manifest = root.join(manifest_name);
        if manifest.exists() {
            return Err(io::Error::new(
                io::ErrorKind::AlreadyExists,
                format!("workspace manifest already exists: {}", manifest.display()),
            ));
        }
        let workspace_id = workspace_id.into();
        if workspace_id.trim().is_empty()
            || workspace_id.contains('/')
            || workspace_id.contains('\\')
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid workspace id",
            ));
        }
        fs::write(
            &manifest,
            format!("version = 1\nworkspace_id = \"{workspace_id}\"\n\n[cli]\nformat = \"json\"\n"),
        )?;
        let workspace = Self::open(root)?;
        for directory in [
            workspace.config_root(),
            workspace.state_root(),
            workspace.run_root(),
            workspace.logs_root(),
            workspace.data_root(),
            workspace.reference_root(),
            workspace.market_connections_root(),
            workspace.child(&["accounts"])?,
            workspace.child(&["credentials"])?,
            workspace.child(&["profiles"])?,
            workspace.child(&["state", "account-locks"])?,
            workspace.child(&["orders", "journals"])?,
            workspace.launch_dir("default")?,
        ] {
            fs::create_dir_all(directory)?;
        }
        Ok(workspace)
    }

    pub fn open(root: impl Into<PathBuf>) -> io::Result<Self> {
        let mut root = root.into().canonicalize()?;
        let mut manifest_path = root.join("workspace.toml");
        if !manifest_path.is_file() && root.join("kairos.toml").is_file() {
            manifest_path = root.join("kairos.toml");
        }
        if !manifest_path.is_file() && root.join(".kairos/kairos.toml").is_file() {
            root = root.join(".kairos");
            manifest_path = root.join("kairos.toml");
        }
        let contents = fs::read_to_string(manifest_path)?;
        let manifest: WorkspaceManifest = toml::from_str(&contents)
            .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
        if manifest.version != 1 || manifest.workspace_id.trim().is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "invalid workspace manifest",
            ));
        }
        if !matches!(manifest.cli.format.as_str(), "text" | "json" | "table") {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "workspace cli.format must be text, json, or table",
            ));
        }
        Ok(Self { root, manifest })
    }

    pub fn id(&self) -> &str {
        &self.manifest.workspace_id
    }
    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn cli_format(&self) -> &str {
        &self.manifest.cli.format
    }

    pub fn market_config(&self) -> &WorkspaceMarketConfig {
        &self.manifest.market
    }

    pub fn reference_config(&self) -> &WorkspaceReferenceConfig {
        &self.manifest.reference
    }

    pub fn config_root(&self) -> PathBuf {
        self.root.join("config")
    }
    pub fn state_root(&self) -> PathBuf {
        self.root.join("state")
    }
    pub fn run_root(&self) -> PathBuf {
        self.root.join("run")
    }
    pub fn logs_root(&self) -> PathBuf {
        self.root.join("logs")
    }
    pub fn data_root(&self) -> PathBuf {
        self.root.join("data")
    }
    pub fn reference_root(&self) -> PathBuf {
        self.root.join("reference")
    }

    pub fn market_connections_root(&self) -> PathBuf {
        self.root.join("market").join("connections")
    }

    pub fn instance(
        &self,
        mode: impl Into<String>,
        launch_id: impl Into<String>,
        instance_id: impl Into<String>,
    ) -> io::Result<InstanceWorkspace> {
        InstanceWorkspace::new(self, mode, launch_id, instance_id)
    }

    pub fn process_dir(&self, name: &str) -> io::Result<PathBuf> {
        self.child(&["run", name])
    }

    pub fn launch_dir(&self, id: &str) -> io::Result<PathBuf> {
        self.child(&["launches", id])
    }

    pub fn child(&self, parts: &[&str]) -> io::Result<PathBuf> {
        if parts.is_empty()
            || parts
                .iter()
                .any(|part| part.is_empty() || *part == "." || *part == "..")
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid workspace child path",
            ));
        }
        let candidate = self.root.join(parts.iter().collect::<PathBuf>());
        if !candidate.starts_with(&self.root) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "workspace path escapes root",
            ));
        }
        Ok(candidate)
    }

    pub fn process_socket(&self, process: &str) -> io::Result<PathBuf> {
        self.control_socket(process)
    }

    pub fn process_lock(&self, process: &str) -> io::Result<WorkspaceProcessLock> {
        let path = self.child(&["run", process, &format!("{process}.lock")])?;
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let file = OpenOptions::new()
            .create(true)
            .read(true)
            .write(true)
            .truncate(false)
            .open(&path)?;

        #[cfg(unix)]
        {
            let result = unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) };
            if result != 0 {
                let error = io::Error::last_os_error();
                if matches!(error.raw_os_error(), Some(code) if code == libc::EAGAIN || code == libc::EWOULDBLOCK)
                {
                    return Err(io::Error::new(
                        io::ErrorKind::AlreadyExists,
                        format!("process lock is already held: {}", path.display()),
                    ));
                }
                return Err(error);
            }
        }

        #[cfg(not(unix))]
        {
            let _ = file;
            return Err(io::Error::new(
                io::ErrorKind::Unsupported,
                "workspace process locks are only implemented on Unix",
            ));
        }

        let _ = std::fs::write(&path, std::process::id().to_string());
        Ok(WorkspaceProcessLock { _file: file, path })
    }

    pub fn control_socket(&self, name: &str) -> io::Result<PathBuf> {
        if name.trim().is_empty()
            || name == "."
            || name == ".."
            || name.contains('/')
            || name.contains('\\')
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid process socket name",
            ));
        }
        let candidate = self.child(&["run", name, &format!("{name}.sock")])?;
        if candidate.to_string_lossy().len() <= 100 {
            return Ok(candidate);
        }
        let input = format!("{}:{}", self.root.display(), name);
        let digest = Sha256::digest(input.as_bytes());
        let short = digest[..10]
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>();
        Ok(PathBuf::from(format!(
            "/tmp/kairos-process-{short}-{name}.sock"
        )))
    }

    pub fn health_file(&self, name: &str) -> io::Result<PathBuf> {
        self.child(&["run", name, "health.json"])
    }

    pub fn service_health(&self, service: &str) -> io::Result<PathBuf> {
        self.health_file(service)
    }

    pub fn service_snapshot(&self, service: &str) -> io::Result<PathBuf> {
        if service.trim().is_empty()
            || service == "."
            || service == ".."
            || service.contains('/')
            || service.contains('\\')
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid service name",
            ));
        }
        self.child(&["snapshots", service, &format!("{service}.snapshot")])
    }
}

#[cfg(test)]
mod tests {
    use super::Workspace;
    use std::{fs, path::Path};

    #[test]
    fn opens_manifest_and_derives_process_paths() {
        let root = tempfile::tempdir().unwrap();
        fs::write(
            root.path().join("workspace.toml"),
            "version = 1\nworkspace_id = \"demo\"\n\n[cli]\nformat = \"text\"\n",
        )
        .unwrap();
        let workspace = Workspace::open(root.path()).unwrap();
        assert_eq!(workspace.id(), "demo");
        assert_eq!(workspace.cli_format(), "text");
        assert!(workspace
            .process_socket("risk")
            .unwrap()
            .ends_with("run/risk/risk.sock"));
        assert!(workspace.state_root().ends_with("state"));
        assert!(workspace
            .health_file("risk")
            .unwrap()
            .ends_with("run/risk/health.json"));
    }

    #[test]
    fn parses_reference_provider_registry_from_workspace_manifest() {
        let root = tempfile::tempdir().unwrap();
        fs::write(
            root.path().join("workspace.toml"),
            "version = 1\nworkspace_id = \"demo\"\n\n[reference.providers.massive]\nenabled = true\ncredential_id = \"massive-readonly\"\nendpoint = \"https://reference.example.test\"\n\n[reference.providers.okx]\nenabled = false\n",
        )
        .unwrap();
        let workspace = Workspace::open(root.path()).unwrap();
        let massive = workspace
            .reference_config()
            .providers
            .get("massive")
            .unwrap();
        assert_eq!(massive.enabled, Some(true));
        assert_eq!(massive.credential_id.as_deref(), Some("massive-readonly"));
        assert_eq!(
            massive.endpoint.as_deref(),
            Some("https://reference.example.test")
        );
        assert_eq!(
            workspace.reference_config().providers["okx"].enabled,
            Some(false)
        );
    }

    #[test]
    fn process_lock_rejects_a_second_owner_until_the_first_is_dropped() {
        let root = tempfile::tempdir().unwrap();
        let workspace = Workspace::init(root.path().join("workspace"), "demo").unwrap();
        let first = workspace.process_lock("reference").unwrap();
        assert!(first.path().ends_with("run/reference/reference.lock"));
        assert_eq!(
            workspace.process_lock("reference").unwrap_err().kind(),
            std::io::ErrorKind::AlreadyExists
        );
        drop(first);
        assert!(workspace.process_lock("reference").is_ok());
    }

    #[test]
    fn exclusive_process_lock_hashes_provider_identity_and_rejects_second_owner() {
        let root = tempfile::tempdir().unwrap();
        let workspace = Workspace::init(root.path().join("workspace"), "demo").unwrap();
        let identity = "ibkr|127.0.0.1|4002|client-id:7";
        let first = workspace
            .exclusive_process_lock("ibkr-client", identity)
            .unwrap();
        let filename = first.path().to_string_lossy();
        assert!(filename.contains("exclusive-ibkr-client-"));
        assert!(!filename.contains("127.0.0.1"));
        assert_eq!(
            workspace
                .exclusive_process_lock("ibkr-client", identity)
                .unwrap_err()
                .kind(),
            std::io::ErrorKind::AlreadyExists
        );
        drop(first);
        assert!(workspace
            .exclusive_process_lock("ibkr-client", identity)
            .is_ok());
    }

    #[test]
    fn exclusive_process_lock_is_enforced_across_processes() {
        const CHILD_ROOT: &str = "KAIROS_EXCLUSIVE_LOCK_CHILD_ROOT";
        const IDENTITY: &str = "ibkr|127.0.0.1|4002|client-id:7";
        if let Some(root) = std::env::var_os(CHILD_ROOT) {
            let workspace = Workspace::open(root).unwrap();
            assert_eq!(
                workspace
                    .exclusive_process_lock("ibkr-client", IDENTITY)
                    .unwrap_err()
                    .kind(),
                std::io::ErrorKind::AlreadyExists
            );
            return;
        }

        let directory = tempfile::tempdir().unwrap();
        let workspace_root = directory.path().join("workspace");
        let workspace = Workspace::init(&workspace_root, "demo").unwrap();
        let first = workspace
            .exclusive_process_lock("ibkr-client", IDENTITY)
            .unwrap();
        let status = std::process::Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "workspace::tests::exclusive_process_lock_is_enforced_across_processes",
                "--nocapture",
            ])
            .env(CHILD_ROOT, &workspace_root)
            .status()
            .unwrap();
        assert!(status.success());
        drop(first);
        assert!(workspace
            .exclusive_process_lock("ibkr-client", IDENTITY)
            .is_ok());
    }

    #[test]
    fn initializes_project_dot_kairos_layout() {
        let project = tempfile::tempdir().unwrap();
        let workspace = Workspace::init_project(project.path(), "demo").unwrap();
        assert!(project.path().join(".kairos/kairos.toml").is_file());
        assert!(workspace.reference_root().is_dir());
        assert!(workspace.child(&["accounts"]).unwrap().is_dir());
        assert!(workspace.child(&["orders", "journals"]).unwrap().is_dir());
    }

    #[test]
    fn instance_socket_uses_short_alias_for_long_paths() {
        let root = tempfile::tempdir().unwrap();
        let workspace = Workspace::init(
            root.path()
                .join("workspace-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"),
            "demo",
        )
        .unwrap();
        let instance = workspace
            .instance(
                "paper",
                "aapl-paper",
                "0df2adc3-b650-4a93-aa47-e3f12fc7cd69",
            )
            .unwrap();
        let socket = instance.socket("market").unwrap();
        assert_eq!(socket.parent().unwrap(), Path::new("/tmp"));
        assert!(socket
            .file_name()
            .unwrap()
            .to_string_lossy()
            .ends_with("-market.sock"));
    }

    #[test]
    fn process_socket_uses_short_alias_for_long_paths() {
        let root = tempfile::tempdir().unwrap();
        let workspace = Workspace::init(
            root.path()
                .join("workspace-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"),
            "demo",
        )
        .unwrap();
        let socket = workspace.process_socket("reference").unwrap();
        assert_eq!(socket.parent().unwrap(), Path::new("/tmp"));
        assert!(socket
            .file_name()
            .unwrap()
            .to_string_lossy()
            .ends_with("-reference.sock"));
    }

    #[test]
    fn derives_canonical_service_snapshot_resources() {
        let root = tempfile::tempdir().unwrap();
        let workspace = Workspace::init(root.path(), "demo").unwrap();
        let instance = workspace.instance("paper", "launch", "default").unwrap();
        assert!(instance
            .service_snapshot("market")
            .unwrap()
            .ends_with("snapshots/market/market.snapshot"));
        assert!(instance
            .service_health("market")
            .unwrap()
            .ends_with("health/market.json"));
        assert!(instance.service_snapshot("../market").is_err());
        assert!(workspace
            .service_snapshot("risk")
            .unwrap()
            .ends_with("snapshots/risk/risk.snapshot"));
    }
}
