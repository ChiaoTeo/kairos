use criterion::{criterion_group, criterion_main, Criterion};
use kairos_primitives::{Price, Quantity};
use kairos_market::PriceLevel;
use std::hint::black_box;

fn canonical_checksum_benchmark(c: &mut Criterion) {
    let price = Price::new(100, 0).unwrap();
    let quantity = Quantity::positive(10, 0).unwrap();
    let book = kairos_market::OrderBook::snapshot(
        "market",
        "BTC-USD",
        1_u64,
        1_u64,
        vec![PriceLevel { price, quantity }],
        vec![PriceLevel { price, quantity }],
    )
    .unwrap();

    c.bench_function("orderbook_canonical_checksum", |b| {
        b.iter(|| black_box(book.canonical_checksum()))
    });
}

criterion_group!(benches, canonical_checksum_benchmark);
criterion_main!(benches);
