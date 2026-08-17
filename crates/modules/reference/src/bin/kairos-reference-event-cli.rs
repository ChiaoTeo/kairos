use clap::Parser;
use kairos_reference_contract::{decode_event, ReferenceEvent};
use kairos_transport::AeronByteSubscription;
use serde_json::json;
use std::time::{Duration, Instant};

#[derive(Debug, Parser)]
#[command(
    name = "kairos-reference-event-cli",
    about = "Observe pushed Reference lifecycle batches"
)]
struct Args {
    #[arg(long)]
    aeron_dir: Option<String>,
    #[arg(long, default_value = kairos_transport::DEFAULT_CHANNEL)]
    aeron_channel: String,
    #[arg(
        long,
        default_value_t = kairos_transport::stream_ids::REFERENCE_CHANGES,
        value_parser = clap::value_parser!(i32).range(1..)
    )]
    stream_id: i32,
    #[arg(long, default_value_t = 30, value_parser = clap::value_parser!(u64).range(1..))]
    timeout_seconds: u64,
    #[arg(long, default_value_t = 2, value_parser = clap::value_parser!(u64).range(1..))]
    idle_timeout_seconds: u64,
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    let mut subscriber = AeronByteSubscription::connect(
        args.aeron_dir.as_deref(),
        &args.aeron_channel,
        args.stream_id,
    )?;
    let deadline = Instant::now() + Duration::from_secs(args.timeout_seconds);
    let mut idle_deadline = None;
    let mut batches = 0usize;
    let mut events = 0usize;
    let mut generation = None;
    let mut event_sequence = None;
    let mut first_event_id = None;
    let mut last_event_id = None;
    loop {
        if let Some(payload) = subscriber.next_frame()? {
            let event = decode_event(&payload)?;
            let (event_id, revision, sequence) = event_metadata(&event);
            batches += 1;
            events += 1;
            generation = Some(revision);
            event_sequence = Some(sequence);
            if first_event_id.is_none() {
                first_event_id = Some(event_id.clone());
            }
            last_event_id = Some(event_id);
            idle_deadline = Some(Instant::now() + Duration::from_secs(args.idle_timeout_seconds));
            continue;
        }
        if idle_deadline.is_some_and(|deadline| Instant::now() >= deadline) {
            break;
        }
        if Instant::now() >= deadline {
            if batches == 0 {
                return Err("no Reference event batch was received before timeout".into());
            }
            break;
        }
        std::thread::sleep(Duration::from_millis(5));
    }
    println!(
        "{}",
        serde_json::to_string(&json!({
            "status": "received",
            "batches": batches,
            "events": events,
            "generation": generation,
            "event_sequence": event_sequence,
            "first_event_id": first_event_id,
            "last_event_id": last_event_id,
        }))?
    );
    Ok(())
}

fn event_metadata(event: &ReferenceEvent<'_>) -> (String, u64, u64) {
    macro_rules! values {
        ($value:expr) => {{
            let metadata = $value.metadata();
            (
                metadata.event_id().to_owned(),
                $value.catalog_revision(),
                metadata.sequence(),
            )
        }};
    }
    match event {
        ReferenceEvent::EntityUpserted(value) => values!(value),
        ReferenceEvent::EntityUpdated(value) => values!(value),
        ReferenceEvent::FinancialProductUpserted(value) => values!(value),
        ReferenceEvent::FinancialProductUpdated(value) => values!(value),
        ReferenceEvent::AssetUpserted(value) => values!(value),
        ReferenceEvent::AssetUpdated(value) => values!(value),
        ReferenceEvent::ExchangeUpserted(value) => values!(value),
        ReferenceEvent::ExchangeUpdated(value) => values!(value),
        ReferenceEvent::ProviderUpserted(value) => values!(value),
        ReferenceEvent::ProviderUpdated(value) => values!(value),
        ReferenceEvent::BrokerUpserted(value) => values!(value),
        ReferenceEvent::BrokerUpdated(value) => values!(value),
        ReferenceEvent::ExecutionAccessUpserted(value) => values!(value),
        ReferenceEvent::ExecutionAccessUpdated(value) => values!(value),
        ReferenceEvent::MarketDataAccessUpserted(value) => values!(value),
        ReferenceEvent::MarketDataAccessUpdated(value) => values!(value),
        ReferenceEvent::InstrumentUpserted(value) => values!(value),
        ReferenceEvent::InstrumentUpdated(value) => values!(value),
        ReferenceEvent::ListingUpserted(value) => values!(value),
        ReferenceEvent::ListingUpdated(value) => values!(value),
        ReferenceEvent::MarketUpserted(value) => values!(value),
        ReferenceEvent::MarketUpdated(value) => values!(value),
    }
}
