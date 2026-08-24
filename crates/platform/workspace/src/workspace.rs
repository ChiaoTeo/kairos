//! Shared workspace identity and layout validation for Rust processes.

use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
#[cfg(unix)]
use std::os::fd::AsRawFd;
use std::path::{Path, PathBuf};

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

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub struct WorkspaceManifest {
    pub version: u32,
    pub workspace_id: String,
    #[serde(default)]
    pub cli: WorkspaceCliConfig,
}

/// Common filesystem categories for a Workspace or launch-instance scope.
#[derive(Debug, Clone)]
pub struct ResourceScope {
    root: PathBuf,
}

impl ResourceScope {
    fn new(root: PathBuf) -> Self {
        Self { root }
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn child(&self, parts: &[&str]) -> io::Result<PathBuf> {
        if parts.is_empty()
            || parts.iter().any(|part| {
                part.is_empty()
                    || *part == "."
                    || *part == ".."
                    || part.contains('/')
                    || part.contains('\\')
            })
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid scope resource path",
            ));
        }
        Ok(self.root.join(parts.iter().collect::<PathBuf>()))
    }

    pub fn config_root(&self) -> PathBuf {
        self.root.join("config")
    }

    pub fn data_root(&self) -> PathBuf {
        self.root.join("data")
    }

    pub fn state_root(&self) -> PathBuf {
        self.root.join("state")
    }

    pub fn snapshots_root(&self) -> PathBuf {
        self.root.join("snapshots")
    }

    pub fn run_root(&self) -> PathBuf {
        self.root.join("run")
    }

    pub fn logs_root(&self) -> PathBuf {
        self.root.join("logs")
    }

    pub fn process_dir(&self, component: &str) -> io::Result<PathBuf> {
        self.child(&["run", component])
    }

    pub fn process_socket(&self, component: &str) -> io::Result<PathBuf> {
        let candidate = self.process_dir(component)?.join("control.sock");
        if candidate.to_string_lossy().len() <= 100 {
            return Ok(candidate);
        }
        let input = format!("{}:{}", self.root.display(), component);
        let digest = Sha256::digest(input.as_bytes());
        let short = digest[..10]
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>();
        Ok(PathBuf::from(format!(
            "/tmp/kairos-process-{short}-{component}.sock"
        )))
    }

    pub fn process_lock_path(&self, component: &str) -> io::Result<PathBuf> {
        Ok(self.process_dir(component)?.join("process.lock"))
    }

    pub fn health_file(&self, component: &str) -> io::Result<PathBuf> {
        Ok(self.process_dir(component)?.join("health.json"))
    }

    pub fn state(&self, parts: &[&str]) -> io::Result<PathBuf> {
        let mut scoped = Vec::with_capacity(parts.len() + 1);
        scoped.push("state");
        scoped.extend_from_slice(parts);
        self.child(&scoped)
    }

    pub fn snapshot(&self, parts: &[&str]) -> io::Result<PathBuf> {
        let mut scoped = Vec::with_capacity(parts.len() + 1);
        scoped.push("snapshots");
        scoped.extend_from_slice(parts);
        self.child(&scoped)
    }
}

#[derive(Debug, Clone)]
pub struct Workspace {
    root: PathBuf,
    manifest: WorkspaceManifest,
    document: toml::Value,
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

/// Workspace-scoped exclusive lease with a monotonically increasing fencing token.
///
/// The advisory lock establishes the current single owner. The durable token
/// distinguishes a new owner from a stale process that retained old in-memory
/// state after a takeover.
#[derive(Debug)]
pub struct WorkspaceFencedLease {
    _lock: WorkspaceProcessLock,
    token_path: PathBuf,
    token: u64,
}

impl WorkspaceFencedLease {
    pub fn token(&self) -> u64 {
        self.token
    }

    pub fn token_path(&self) -> &Path {
        &self.token_path
    }

    pub fn validate(&self) -> io::Result<()> {
        let current = read_fencing_token(&self.token_path)?;
        if current != self.token {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                format!(
                    "fencing token is stale: held={}, current={current}",
                    self.token
                ),
            ));
        }
        Ok(())
    }
}

impl WorkspaceProcessLock {
    pub fn path(&self) -> &Path {
        &self.path
    }
}

#[cfg(unix)]
impl Drop for WorkspaceProcessLock {
    fn drop(&mut self) {
        // Make release deterministic after a failed non-blocking re-entry.
        // Relying only on descriptor close is observably racy on macOS.
        unsafe {
            libc::flock(self._file.as_raw_fd(), libc::LOCK_UN);
        }
    }
}

#[derive(Debug, Clone)]
pub struct InstanceWorkspace {
    resources: ResourceScope,
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
        let root = workspace
            .root
            .join("launches")
            .join(&mode)
            .join(&launch_id)
            .join("instances")
            .join(&instance_id);
        Ok(Self {
            resources: ResourceScope::new(root),
            mode,
            launch_id,
            instance_id,
        })
    }

    pub fn root(&self) -> PathBuf {
        self.resources.root().to_path_buf()
    }

    pub fn paths(&self) -> &ResourceScope {
        &self.resources
    }

    pub fn socket(&self, name: &str) -> io::Result<PathBuf> {
        self.resources.process_socket(Self::component(name)?)
    }

    pub fn health(&self, name: &str) -> io::Result<PathBuf> {
        self.resources.health_file(Self::component(name)?)
    }

    pub fn process_lock(&self, name: &str) -> io::Result<WorkspaceProcessLock> {
        let name = Self::component(name)?;
        acquire_process_lock(self.resources.process_lock_path(name)?)
    }

    pub fn component_manifest(&self) -> io::Result<PathBuf> {
        Ok(self.root().join("manifest.json"))
    }

    pub fn service_health(&self, service: &str) -> io::Result<PathBuf> {
        self.health(service)
    }

    pub fn state(&self, parts: &[&str]) -> io::Result<PathBuf> {
        self.resources.state(Self::components(parts)?)
    }

    pub fn snapshot(&self, parts: &[&str]) -> io::Result<PathBuf> {
        self.resources.snapshot(Self::components(parts)?)
    }

    pub fn service_snapshot(&self, service: &str) -> io::Result<PathBuf> {
        let service = Self::component(service)?;
        self.snapshot(&[service, &format!("{service}.snapshot")])
    }

    pub fn normalized_config(&self) -> io::Result<PathBuf> {
        self.resources.child(&["config", "normalized.json"])
    }

    pub fn lifecycle_journal(&self) -> io::Result<PathBuf> {
        self.state(&["launch", "lifecycle.jsonl"])
    }

    pub fn checkpoint(&self, component: &str, name: &str) -> io::Result<PathBuf> {
        self.state(&[
            Self::component(component)?,
            "checkpoints",
            Self::component(name)?,
        ])
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
        fs::create_dir_all(self.root())
    }
}

impl Workspace {
    /// Acquires a workspace-wide lease for an external resource that permits
    /// only one owning process. The resource identity is hashed so endpoints
    /// and account-like identifiers are not exposed in lock filenames.
    pub fn exclusive_process_lock(
        &self,
        namespace: &str,
        resource_identity: &str,
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
        if resource_identity.trim().is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "exclusive process lock identity is required",
            ));
        }
        let digest = Sha256::digest(resource_identity.as_bytes());
        let short = digest[..10]
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>();
        self.process_lock(&format!("exclusive-{namespace}-{short}"))
    }

    /// Acquire one workspace-wide writer lease and advance its durable epoch.
    pub fn fenced_lease(
        &self,
        namespace: &str,
        resource_identity: &str,
    ) -> io::Result<WorkspaceFencedLease> {
        if namespace.trim().is_empty()
            || !namespace
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "fenced lease namespace is invalid",
            ));
        }
        if resource_identity.trim().is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "fenced lease identity is required",
            ));
        }
        let digest = Sha256::digest(resource_identity.as_bytes());
        let short = digest[..10]
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>();
        let lock = self.process_lock(&format!("fenced-{namespace}-{short}"))?;
        let token_path = self.child(&["state", "leases", &format!("{namespace}-{short}.epoch")])?;
        if let Some(parent) = token_path.parent() {
            fs::create_dir_all(parent)?;
        }
        let token = if token_path.exists() {
            read_fencing_token(&token_path)?
                .checked_add(1)
                .ok_or_else(|| io::Error::other("fencing token exhausted"))?
        } else {
            1
        };
        let temporary = token_path.with_extension(format!("epoch.{}.tmp", std::process::id()));
        fs::write(&temporary, token.to_string())?;
        fs::rename(&temporary, &token_path)?;
        Ok(WorkspaceFencedLease {
            _lock: lock,
            token_path,
            token,
        })
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
        let document: toml::Value = toml::from_str(&contents)
            .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
        let manifest: WorkspaceManifest = document
            .clone()
            .try_into()
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
        Ok(Self {
            root,
            manifest,
            document,
        })
    }

    pub fn id(&self) -> &str {
        &self.manifest.workspace_id
    }
    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn paths(&self) -> ResourceScope {
        ResourceScope::new(self.root.clone())
    }

    pub fn cli_format(&self) -> &str {
        &self.manifest.cli.format
    }

    /// Decode one business-owned manifest section without teaching Workspace
    /// its schema. Missing sections decode from an empty table.
    pub fn read_section<T: serde::de::DeserializeOwned>(&self, section: &str) -> io::Result<T> {
        let value = self
            .document
            .get(section)
            .cloned()
            .unwrap_or_else(|| toml::Value::Table(Default::default()));
        value
            .try_into()
            .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))
    }

    pub fn config_root(&self) -> PathBuf {
        self.paths().config_root()
    }
    pub fn state_root(&self) -> PathBuf {
        self.paths().state_root()
    }
    pub fn run_root(&self) -> PathBuf {
        self.paths().run_root()
    }
    pub fn logs_root(&self) -> PathBuf {
        self.paths().logs_root()
    }
    pub fn data_root(&self) -> PathBuf {
        self.paths().data_root()
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
        self.paths().process_dir(name)
    }

    pub fn launch_dir(&self, id: &str) -> io::Result<PathBuf> {
        self.child(&["launches", id])
    }

    pub fn child(&self, parts: &[&str]) -> io::Result<PathBuf> {
        self.paths().child(parts)
    }

    /// Resolve a canonical resource for writing, or an existing legacy path
    /// for a bounded read-only migration.
    pub fn existing_path(
        &self,
        canonical_parts: &[&str],
        legacy_parts: &[&str],
    ) -> io::Result<PathBuf> {
        let canonical = self.child(canonical_parts)?;
        if canonical.exists() {
            return Ok(canonical);
        }
        let legacy = self.child(legacy_parts)?;
        Ok(if legacy.exists() { legacy } else { canonical })
    }

    pub fn process_socket(&self, process: &str) -> io::Result<PathBuf> {
        self.control_socket(process)
    }

    pub fn process_lock(&self, process: &str) -> io::Result<WorkspaceProcessLock> {
        acquire_process_lock(self.paths().process_lock_path(process)?)
    }

    pub fn control_socket(&self, name: &str) -> io::Result<PathBuf> {
        self.paths().process_socket(name)
    }

    pub fn health_file(&self, name: &str) -> io::Result<PathBuf> {
        self.paths().health_file(name)
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
        self.paths()
            .snapshot(&[service, &format!("{service}.snapshot")])
    }
}

fn acquire_process_lock(path: PathBuf) -> io::Result<WorkspaceProcessLock> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut file = OpenOptions::new()
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

    file.set_len(0)?;
    file.write_all(std::process::id().to_string().as_bytes())?;
    Ok(WorkspaceProcessLock { _file: file, path })
}

fn read_fencing_token(path: &Path) -> io::Result<u64> {
    let value = fs::read_to_string(path)?;
    value.trim().parse().map_err(|error| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("invalid fencing token at {}: {error}", path.display()),
        )
    })
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::path::Path;

    use super::Workspace;

    #[test]
    fn production_workspace_source_has_no_provider_or_business_schema() {
        let source = include_str!("workspace.rs")
            .split("#[cfg(test)]")
            .next()
            .unwrap();
        for forbidden in [
            "Binance",
            "Okx",
            "Hyperliquid",
            "Massive",
            "MarketSourceBinding",
            "ReferenceProviderConfig",
            "AccountBindingRecord",
            "reference_root",
            "market_connections_root",
            "market_state",
            "API_KEY",
            "API_SECRET",
        ] {
            assert!(!source.contains(forbidden), "Workspace owns {forbidden}");
        }
    }

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
        assert!(
            workspace
                .process_socket("risk")
                .unwrap()
                .ends_with("run/risk/control.sock")
        );
        assert!(workspace.state_root().ends_with("state"));
        assert!(
            workspace
                .health_file("risk")
                .unwrap()
                .ends_with("run/risk/health.json")
        );
    }

    #[test]
    fn exposes_business_sections_without_owning_their_schema() {
        let root = tempfile::tempdir().unwrap();
        fs::write(
            root.path().join("workspace.toml"),
            "version = 1\nworkspace_id = \"demo\"\n\n[reference.providers.massive]\nenabled = true\ncredential_id = \"massive-readonly\"\nendpoint = \"https://reference.example.test\"\n\n[reference.providers.okx]\nenabled = false\n",
        )
        .unwrap();
        let workspace = Workspace::open(root.path()).unwrap();
        let reference: toml::Value = workspace.read_section("reference").unwrap();
        assert_eq!(
            reference["providers"]["massive"]["credential_id"].as_str(),
            Some("massive-readonly")
        );
        assert_eq!(
            reference["providers"]["okx"]["enabled"].as_bool(),
            Some(false)
        );
    }

    #[test]
    fn reads_market_section_as_opaque_data() {
        let root = tempfile::tempdir().unwrap();
        fs::write(
            root.path().join("workspace.toml"),
            r#"version = 1
workspace_id = "demo"

[market.collections.btc-bars]
market_id = "market:binance:spot:BTCUSDT"
observations = ["bar"]
exchange = "binance"
market_type = "spot"
provider = "binance"

[[market.providers]]
type = "binance-spot"
"#,
        )
        .unwrap();

        let workspace = Workspace::open(root.path()).unwrap();
        let market: toml::Value = workspace.read_section("market").unwrap();
        let collection = &market["collections"]["btc-bars"];
        assert_eq!(
            collection["market_id"].as_str(),
            Some("market:binance:spot:BTCUSDT")
        );
        assert_eq!(collection["provider"].as_str(), Some("binance"));
    }

    #[test]
    fn process_lock_rejects_a_second_owner_until_the_first_is_dropped() {
        let root = tempfile::tempdir().unwrap();
        let workspace = Workspace::init(root.path().join("workspace"), "demo").unwrap();
        let first = workspace.process_lock("reference").unwrap();
        assert!(first.path().ends_with("run/reference/process.lock"));
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
        assert!(
            workspace
                .exclusive_process_lock("ibkr-client", identity)
                .is_ok()
        );
    }

    #[test]
    fn fenced_lease_is_exclusive_and_advances_monotonic_token_on_takeover() {
        let root = tempfile::tempdir().unwrap();
        let workspace = Workspace::init(root.path().join("workspace"), "demo").unwrap();
        let identity = "binance|live|principal:main|account:main|segment:spot";
        let first = workspace
            .fenced_lease("execution-writer", identity)
            .unwrap();
        assert_eq!(first.token(), 1);
        assert!(first.validate().is_ok());
        fs::write(first.token_path(), "9").unwrap();
        assert_eq!(
            first.validate().unwrap_err().kind(),
            std::io::ErrorKind::PermissionDenied
        );
        fs::write(first.token_path(), "1").unwrap();
        assert_eq!(
            workspace
                .fenced_lease("execution-writer", identity)
                .unwrap_err()
                .kind(),
            std::io::ErrorKind::AlreadyExists
        );
        let token_path = first.token_path().to_path_buf();
        drop(first);

        let second = workspace
            .fenced_lease("execution-writer", identity)
            .unwrap();
        assert_eq!(second.token(), 2);
        assert_eq!(second.token_path(), token_path);
        assert!(second.validate().is_ok());
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
        assert!(
            workspace
                .exclusive_process_lock("ibkr-client", IDENTITY)
                .is_ok()
        );
    }

    #[test]
    fn initializes_project_dot_kairos_layout() {
        let project = tempfile::tempdir().unwrap();
        let workspace = Workspace::init_project(project.path(), "demo").unwrap();
        assert!(project.path().join(".kairos/kairos.toml").is_file());
        assert!(workspace.config_root().is_dir());
        assert!(workspace.state_root().is_dir());
        assert!(!workspace.child(&["accounts"]).unwrap().exists());
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
        assert!(
            socket
                .file_name()
                .unwrap()
                .to_string_lossy()
                .ends_with("-market.sock")
        );
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
        assert!(
            socket
                .file_name()
                .unwrap()
                .to_string_lossy()
                .ends_with("-reference.sock")
        );
    }

    #[test]
    fn derives_canonical_service_snapshot_resources() {
        let root = tempfile::tempdir().unwrap();
        let workspace = Workspace::init(root.path(), "demo").unwrap();
        let instance = workspace.instance("paper", "launch", "default").unwrap();
        assert!(
            instance
                .service_snapshot("market")
                .unwrap()
                .ends_with("snapshots/market/market.snapshot")
        );
        assert!(
            instance
                .service_health("market")
                .unwrap()
                .ends_with("run/market/health.json")
        );
        assert!(instance.service_snapshot("../market").is_err());
        assert!(
            workspace
                .service_snapshot("risk")
                .unwrap()
                .ends_with("snapshots/risk/risk.snapshot")
        );
    }

    #[test]
    fn workspace_and_instance_scopes_share_the_runtime_layout() {
        let root = tempfile::tempdir().unwrap();
        let workspace = Workspace::init(root.path(), "demo").unwrap();
        let instance = workspace.instance("paper", "launch", "run").unwrap();
        instance.prepare().unwrap();

        assert!(
            workspace
                .paths()
                .process_dir("market")
                .unwrap()
                .ends_with("run/market")
        );
        assert!(
            instance
                .paths()
                .process_dir("market")
                .unwrap()
                .ends_with("run/market")
        );
        assert!(
            instance
                .health("market")
                .unwrap()
                .ends_with("run/market/health.json")
        );
        assert!(
            instance
                .normalized_config()
                .unwrap()
                .ends_with("config/normalized.json")
        );
        assert!(
            instance
                .checkpoint("market", "replay.json")
                .unwrap()
                .ends_with("state/market/checkpoints/replay.json")
        );
        assert!(
            instance
                .component_manifest()
                .unwrap()
                .ends_with("manifest.json")
        );
        for legacy in ["sockets", "health", "locks", "checkpoints"] {
            assert!(!instance.root().join(legacy).exists());
        }
    }
}
