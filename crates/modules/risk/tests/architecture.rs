use std::fs;
use std::path::{Path, PathBuf};

fn rust_files(root: &Path) -> Vec<PathBuf> {
    let mut files = Vec::new();
    for entry in fs::read_dir(root).expect("read source directory") {
        let path = entry.expect("read source entry").path();
        if path.is_dir() {
            files.extend(rust_files(&path));
        } else if path.extension().and_then(|value| value.to_str()) == Some("rs") {
            files.push(path);
        }
    }
    files
}

#[test]
fn risk_application_does_not_publish_persistence_protocols() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src");
    assert!(!root.join("application/protocol.rs").exists());
    let application = fs::read_to_string(root.join("application/mod.rs")).unwrap();
    assert!(!application.contains("RiskStateStore"));
}

#[test]
fn risk_domain_has_no_infrastructure_dependencies() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/domain");
    for path in rust_files(&root) {
        let source = fs::read_to_string(&path).unwrap();
        let shared_types_only = source
            .replace("kairos_primitives", "")
            .replace("kairos-primitives", "");
        assert!(!shared_types_only.contains("kairos_") && !source.contains("std::fs"));
    }
}
