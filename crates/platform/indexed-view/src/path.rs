use std::path::{Path, PathBuf};

use crate::{IndexedViewIdentity, StoreError};

/// Resolve an epoch-specific environment without scanning the filesystem.
pub fn environment_path(
    runtime_resource_root: impl AsRef<Path>,
    identity: &IndexedViewIdentity,
) -> Result<PathBuf, StoreError> {
    let root = runtime_resource_root.as_ref();
    if !root.is_absolute() {
        return Err(StoreError::InvalidPath(
            "runtime resource root must be absolute".into(),
        ));
    }
    Ok(root
        .join("views")
        .join("v3")
        .join(component(&identity.owner)?)
        .join(component(&identity.publisher_resource_id)?)
        .join(format!("epoch-{}", identity.resource_epoch))
        .join("current.lmdb"))
}

fn component(value: &str) -> Result<String, StoreError> {
    if value.is_empty() || value.trim() != value || value.as_bytes().contains(&0) {
        return Err(StoreError::InvalidPath(
            "resource path components must be non-empty, trimmed UTF-8 without NUL".into(),
        ));
    }
    Ok(value
        .as_bytes()
        .iter()
        .map(|byte| {
            if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.') {
                (*byte as char).to_string()
            } else {
                format!("%{byte:02X}")
            }
        })
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::SchemaSet;

    #[test]
    fn path_is_epoch_specific_and_escaped() {
        let identity = IndexedViewIdentity::new(
            "workspace/一",
            Some("launch"),
            Some("instance"),
            "Execution",
            "publisher/../one",
            7,
            9,
            SchemaSet::new([]).unwrap(),
        )
        .unwrap();
        let path = environment_path(Path::new("/runtime"), &identity).unwrap();
        assert!(path.starts_with("/runtime/views/v3"));
        assert!(path.ends_with("Execution/publisher%2F..%2Fone/epoch-7/current.lmdb"));
        assert!(!path.to_string_lossy().contains("/../"));
    }
}
