use clap::Parser;
use std::io::{self, Write};
use std::time::Duration;

#[derive(Debug, Parser)]
#[command(
    name = "kairos-market-event-bridge",
    about = "Bridge Market Aeron frames to a length-prefixed stdout stream"
)]
struct Args {
    #[arg(long, env = "AERON_DIR")]
    aeron_dir: Option<String>,

    #[arg(long, default_value = kairos_transport::DEFAULT_CHANNEL)]
    aeron_channel: String,

    #[arg(
        long,
        default_value_t = kairos_transport::stream_ids::MARKET_EVENTS,
        value_parser = clap::value_parser!(i32).range(1..)
    )]
    stream_id: i32,
    /// Connect the configured subscription and exit without consuming frames.
    #[arg(long)]
    check: bool,
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    let mut subscription = kairos_transport::AeronByteSubscription::connect(
        args.aeron_dir.as_deref(),
        &args.aeron_channel,
        args.stream_id,
    )?;
    if args.check {
        return Ok(());
    }
    let mut output = io::BufWriter::new(io::stdout().lock());
    loop {
        if let Some(frame) = subscription.next_frame()? {
            let size = u32::try_from(frame.len())?;
            output.write_all(&size.to_be_bytes())?;
            output.write_all(&frame)?;
            output.flush()?;
        } else {
            std::thread::sleep(Duration::from_millis(2));
        }
    }
}
