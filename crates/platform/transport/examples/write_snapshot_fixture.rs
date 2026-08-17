use kairos_transport::{SharedSnapshotWriter, SnapshotEnvelopeMetadata};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let path = std::env::args_os()
        .nth(1)
        .ok_or("usage: write_snapshot_fixture PATH")?;
    let mut writer = SharedSnapshotWriter::create(path, 128)?;
    writer.publish_with_metadata(
        SnapshotEnvelopeMetadata {
            resource_epoch: 2,
            producer_incarnation: 3,
            generation: 7,
            applied_event_sequence: 11,
            published_at_unix_nanos: 2_000,
        },
        b"cross-language-payload",
    )?;
    Ok(())
}
