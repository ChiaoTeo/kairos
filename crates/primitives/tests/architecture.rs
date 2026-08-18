use std::{fs, path::PathBuf};

fn crate_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn rust_sources(directory: PathBuf) -> Vec<PathBuf> {
    let mut pending = vec![directory];
    let mut sources = Vec::new();
    while let Some(directory) = pending.pop() {
        for entry in fs::read_dir(directory).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                pending.push(path);
            } else if path.extension().is_some_and(|extension| extension == "rs") {
                sources.push(path);
            }
        }
    }
    sources
}

#[test]
fn production_source_does_not_depend_on_business_or_platform_crates() {
    for path in rust_sources(crate_root().join("src")) {
        let source = fs::read_to_string(&path).unwrap();
        for forbidden in [
            "kairos_account",
            "kairos_execution",
            "kairos_market",
            "kairos_reference",
            "kairos_risk",
            "kairos_integration",
            "kairos_workspace",
            "flatbuffers",
            "sqlx",
            "rusqlite",
        ] {
            assert!(
                !source.contains(forbidden),
                "{}: shared primitives must not depend on {forbidden}",
                path.display()
            );
        }
    }
}

#[test]
fn manifest_keeps_the_production_dependency_allowlist_small() {
    let manifest = fs::read_to_string(crate_root().join("Cargo.toml")).unwrap();
    let production = manifest
        .split("[dependencies]")
        .nth(1)
        .unwrap()
        .split("[dev-dependencies]")
        .next()
        .unwrap();
    let mut dependencies: Vec<_> = production
        .lines()
        .filter_map(|line| line.split_once('=').map(|(name, _)| name.trim()))
        .filter(|name| !name.is_empty())
        .map(|name| name.strip_suffix(".workspace").unwrap_or(name))
        .collect();
    dependencies.sort_unstable();

    assert_eq!(dependencies, ["rust_decimal", "serde"]);
}
