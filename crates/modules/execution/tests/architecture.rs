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
fn execution_public_boundaries_do_not_expose_decimal_storage_parts() {
    let service_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let roots = [
        service_root.join("src/application"),
        service_root.join("src/bin"),
        service_root.join("../contract/src"),
    ];

    for root in roots {
        for path in rust_files(&root) {
            let source = fs::read_to_string(&path).expect("read boundary source");
            for forbidden in [
                "pub quantity_mantissa",
                "pub quantity_scale",
                "pub price_mantissa",
                "pub price_scale",
            ] {
                assert!(
                    !source.contains(forbidden),
                    "public decimal storage part {forbidden} leaked through {}",
                    path.display()
                );
            }
        }
    }
}

#[test]
fn execution_cli_accepts_semantic_decimal_arguments() {
    let cli = fs::read_to_string(
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/bin/kairos-execution-cli.rs"),
    )
    .expect("read execution CLI");
    for expected in [
        "quantity: String",
        "limit_price: Option<String>",
        "price: String",
    ] {
        assert!(cli.contains(expected), "CLI is missing {expected}");
    }
    for forbidden in [
        "quantity_mantissa",
        "quantity_scale",
        "price_mantissa",
        "price_scale",
    ] {
        assert!(!cli.contains(forbidden), "CLI exposes {forbidden}");
    }
}

#[test]
fn execution_does_not_reintroduce_cross_provider_product_aliases() {
    let source_root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src");
    for path in rust_files(&source_root) {
        let source = fs::read_to_string(&path).expect("read Execution source");
        for forbidden in [
            "RouteProduct",
            "product_matches",
            "\"usd-m-futures\" | \"swap\"",
            "\"swap\" | \"usd-m-futures\"",
            "\"coin-m-futures\" | \"futures\"",
            "\"futures\" | \"coin-m-futures\"",
        ] {
            assert!(
                !source.contains(forbidden),
                "cross-provider product alias {forbidden} leaked through {}",
                path.display()
            );
        }
    }
}
