use std::hint::black_box;

use criterion::{BatchSize, Criterion, Throughput, criterion_group, criterion_main};
use kairos_market::{MarketApplication, OrderBookDelta, PriceLevel};
use kairos_primitives::decimal::{Price, Quantity};
use kairos_primitives::market::Provider;
use kairos_primitives::reference::{InstrumentId, MarketId};
use kairos_primitives::time::{Sequence, UnixNanos};

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

fn orderbook_delta_batch_benchmark(c: &mut Criterion) {
    const LEVELS: usize = 1_000;
    const DELTAS: u64 = 100;
    let provider = Provider::new("benchmark").unwrap();
    let market_id = MarketId::new("market:benchmark").unwrap();
    let instrument_id = InstrumentId::new("instrument:benchmark").unwrap();
    let levels = (0..LEVELS)
        .map(|index| PriceLevel {
            price: Price::new(100_000 + index as i64, 2).unwrap(),
            quantity: Quantity::positive(10, 0).unwrap(),
        })
        .collect::<Vec<_>>();
    let snapshot = kairos_market::OrderBook::snapshot_with_provider(
        provider.clone(),
        market_id.to_string(),
        instrument_id.to_string(),
        1_u64,
        1_u64,
        levels,
        vec![],
    )
    .unwrap();

    let mut group = c.benchmark_group("market_orderbook_publication_batch");
    group.throughput(Throughput::Elements(DELTAS));
    group.bench_function("100_deltas_1000_levels_ingest_and_drain", |b| {
        b.iter_batched(
            || {
                let mut application = MarketApplication::new("benchmark", 1_024).unwrap();
                application
                    .ingest_orderbook_snapshot(snapshot.clone())
                    .unwrap();
                application.drain_changes_limited(1_024);
                application
            },
            |mut application| {
                for sequence in 2..=DELTAS + 1 {
                    application
                        .ingest_orderbook_delta(OrderBookDelta {
                            provider: provider.clone(),
                            market_id: market_id.clone(),
                            instrument_id: instrument_id.clone(),
                            first_sequence: Sequence::new(sequence),
                            last_sequence: Sequence::new(sequence),
                            event_time_unix_nanos: UnixNanos::new(sequence),
                            bids: vec![PriceLevel {
                                price: Price::new(100_000 + sequence as i64, 2).unwrap(),
                                quantity: Quantity::positive(sequence as i64, 0).unwrap(),
                            }],
                            asks: vec![],
                            checksum: None,
                        })
                        .unwrap();
                }
                black_box(application.drain_changes_limited(1_024));
            },
            BatchSize::SmallInput,
        )
    });
    group.finish();
}

criterion_group!(
    benches,
    canonical_checksum_benchmark,
    orderbook_delta_batch_benchmark
);
criterion_main!(benches);
