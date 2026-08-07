//! Shared workspace identity and layout validation for Rust processes.

use std::{
    fs, io,
    path::{Path, PathBuf},
};

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

#[derive(Debug, Clone)]
pub struct Workspace {
    root: PathBuf,
    manifest: WorkspaceManifest,
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
        if candidate.to_string_lossy().as_bytes().len() <= 100 {
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
            self.root().join("market"),
        ] {
            fs::create_dir_all(directory)?;
        }
        Ok(())
    }
}

impl Workspace {
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
        if !matches!(manifest.cli.format.as_str(), "text" | "json") {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "workspace cli.format must be text or json",
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

    pub fn control_socket(&self, name: &str) -> io::Result<PathBuf> {
        self.child(&["run", name, &format!("{name}.sock")])
    }

    pub fn health_file(&self, name: &str) -> io::Result<PathBuf> {
        self.child(&["run", name, "health.json"])
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
}
