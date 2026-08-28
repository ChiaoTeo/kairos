use criterion::{BenchmarkId, Criterion, Throughput, criterion_group, criterion_main};

fn borrowed_frame_baseline(c: &mut Criterion) {
    let mut group = c.benchmark_group("aeron_frame_handoff");
    for size in [512_usize, 64 * 1024] {
        let frame = vec![0x5a; size];
        group.throughput(Throughput::Bytes(size as u64));
        group.bench_with_input(BenchmarkId::new("borrowed", size), &frame, |b, frame| {
            b.iter(|| std::hint::black_box(std::hint::black_box(frame).as_slice()))
        });
        group.bench_with_input(BenchmarkId::new("owned_copy", size), &frame, |b, frame| {
            b.iter(|| std::hint::black_box(std::hint::black_box(frame).to_vec()))
        });
    }
    group.finish();
}

criterion_group!(benches, borrowed_frame_baseline);
criterion_main!(benches);
