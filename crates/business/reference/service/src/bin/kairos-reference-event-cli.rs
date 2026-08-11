use clap::Parser;
use kairos_reference_contract::decode_change;
use kairos_reference_contract::transport::AeronEventSubscriber;
use serde_json::json;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

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
    let mut subscriber = AeronEventSubscriber::connect(
        args.aeron_dir.as_deref(),
        &args.aeron_channel,
        args.stream_id,
        "reference.lifecycle",
        1,
        "reference-actor",
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
        if let Some(envelope) = subscriber.next(0, unix_nanos())? {
            let change = decode_change(&envelope.payload)?;
            batches += 1;
            events += change.events.len();
            generation = Some(change.generation);
            event_sequence = Some(change.event_sequence);
            if first_event_id.is_none() {
                first_event_id = change.events.first().map(|event| event.event_id.clone());
            }
            if let Some(event) = change.events.last() {
                last_event_id = Some(event.event_id.clone());
            }
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

fn unix_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}
