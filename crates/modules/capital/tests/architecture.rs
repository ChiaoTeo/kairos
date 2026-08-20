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
fn capital_domain_has_no_infrastructure_or_other_business_module_dependencies() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/domain");
    for path in rust_files(&root) {
        let source = fs::read_to_string(&path).unwrap();
        let shared_types_only = source
            .replace("kairos_primitives", "")
            .replace("kairos-primitives", "");
        assert!(!shared_types_only.contains("kairos_") && !source.contains("std::fs"));
    }
}

#[test]
fn capital_application_does_not_publish_provider_or_persistence_ports() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src");
    assert!(!root.join("application/ports.rs").exists());
    let application = fs::read_to_string(root.join("application/mod.rs")).unwrap();
    assert!(!application.contains("Binance"));
    assert!(!application.contains("StateStore"));
}

#[test]
fn capital_uses_conflux_instead_of_integration_directly() {
    let crate_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let manifest = fs::read_to_string(crate_root.join("Cargo.toml")).unwrap();
    assert!(manifest.contains("kairos-conflux.workspace = true"));
    assert!(!manifest.contains("kairos-integration"));
    for path in rust_files(&crate_root.join("src")) {
        let source = fs::read_to_string(&path).unwrap();
        assert!(
            !source.contains("kairos_integration"),
            "{} bypasses Conflux",
            path.display()
        );
        assert!(
            !source.contains("BinanceCapitalRestConnection"),
            "{} constructs an Integration connection instead of using Conflux",
            path.display()
        );
        assert!(
            !source.contains("BinanceTransferAccount"),
            "{} imports a provider transfer vocabulary instead of using Conflux",
            path.display()
        );
    }

    let process = fs::read_to_string(crate_root.join("src/application/process.rs")).unwrap();
    assert!(process.contains("CapitalTransferConnection"));
    assert!(process.contains("CapitalEarnConnection"));
    assert!(!process.contains("IntegrationError"));
    let import_start = process.find("use kairos_conflux::{").unwrap();
    let import_end = process[import_start..].find("};").unwrap() + import_start;
    let imports = process[import_start + "use kairos_conflux::{".len()..import_end]
        .split(',')
        .map(str::trim)
        .filter(|name| !name.is_empty());
    for imported_type in imports {
        assert!(
            imported_type.starts_with("Capital"),
            "Capital process imports non-Capital Conflux vocabulary {imported_type}"
        );
    }
}
