use std::path::PathBuf;

use kairos_indexed_view::{
    EnvironmentOptions, IndexedViewIdentity, IndexedViewWriter, Mutation, SchemaDescriptor,
    SchemaSet,
};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let path = std::env::args_os()
        .nth(1)
        .map(PathBuf::from)
        .ok_or("usage: write_fixture <absolute-environment-path>")?;
    let schemas = SchemaSet::new([
        SchemaDescriptor::new("orders", 1, "EO03", 1)?,
        SchemaDescriptor::new("intents", 1, "EI03", 1)?,
    ])?;
    let identity = IndexedViewIdentity::new(
        "workspace",
        Some("launch"),
        Some("instance"),
        "Execution",
        "execution-main",
        1,
        3,
        schemas,
    )?;
    let options = EnvironmentOptions::new(path, 8 * 1024 * 1024)?;
    let mut writer = IndexedViewWriter::create(&options, identity)?;
    writer.apply(
        &[
            Mutation::Put {
                database: "orders".into(),
                key: b"order/1".to_vec(),
                value: b"open".to_vec(),
            },
            Mutation::Put {
                database: "orders".into(),
                key: b"order/2".to_vec(),
                value: b"pending".to_vec(),
            },
            Mutation::Put {
                database: "intents".into(),
                key: b"intent/1".to_vec(),
                value: b"active".to_vec(),
            },
        ],
        11,
        2_000,
    )?;
    Ok(())
}
