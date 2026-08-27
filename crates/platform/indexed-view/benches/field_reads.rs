use criterion::{Criterion, Throughput, criterion_group, criterion_main};
use kairos_indexed_view::{
    EnvironmentOptions, IndexedViewIdentity, IndexedViewReader, IndexedViewWriter, Mutation,
    SchemaDescriptor, SchemaSet, environment_path,
};

fn benchmark(c: &mut Criterion) {
    let root = tempfile::tempdir().expect("temporary benchmark directory");
    let identity = IndexedViewIdentity::new(
        "workspace",
        Some("launch"),
        Some("instance"),
        "Market",
        "market-main",
        1,
        1,
        SchemaSet::new([SchemaDescriptor::new("values", 1, "VAL1", 1).unwrap()]).unwrap(),
    )
    .unwrap();
    let options = EnvironmentOptions::new(
        environment_path(root.path(), &identity).unwrap(),
        8 * 1024 * 1024,
    )
    .unwrap();
    let mut writer = IndexedViewWriter::create(&options, identity.clone()).unwrap();
    let mut payload = vec![0_u8; 1024 * 1024];
    payload[..8].copy_from_slice(&42_u64.to_le_bytes());
    writer
        .apply(
            &[Mutation::Put {
                database: "values".into(),
                key: b"entity".to_vec(),
                value: payload,
            }],
            1,
            1,
        )
        .unwrap();
    drop(writer);
    let reader = IndexedViewReader::open(&options, identity).unwrap();

    let mut group = c.benchmark_group("indexed_view_exact_read_1mib");
    group.throughput(Throughput::Bytes(1024 * 1024));
    group.bench_function("owned_full_value", |b| {
        b.iter(|| {
            reader
                .value_snapshot("values", b"entity")
                .unwrap()
                .value
                .unwrap()[0]
        })
    });
    group.bench_function("borrowed_selected_field", |b| {
        b.iter(|| {
            reader
                .with_value_snapshot("values", b"entity", |_metadata, value| {
                    u64::from_le_bytes(value.unwrap()[..8].try_into().unwrap())
                })
                .unwrap()
        })
    });
    group.finish();
}

criterion_group!(benches, benchmark);
criterion_main!(benches);
