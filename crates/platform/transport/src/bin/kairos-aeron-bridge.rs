use clap::Parser;
use std::io::{self, Write};
use std::time::Duration;

#[derive(Debug, Parser)]
#[command(
    name = "kairos-aeron-bridge",
    about = "Diagnostic bridge from one Aeron byte stream to framed stdout"
)]
struct Args {
    #[arg(long, env = "AERON_DIR")]
    aeron_dir: Option<String>,
    #[arg(long, default_value = kairos_transport::DEFAULT_CHANNEL)]
    channel: String,
    #[arg(long, value_parser = clap::value_parser!(i32).range(1..))]
    stream_id: i32,
    #[arg(long, default_value_t = kairos_transport::DEFAULT_MAX_PAYLOAD_LEN)]
    max_frame_len: usize,
    #[arg(long)]
    check: bool,
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    if args.max_frame_len == 0 || args.max_frame_len > u32::MAX as usize {
        return Err("max-frame-len must be in the u32 framing range".into());
    }
    let mut subscription = kairos_transport::AeronByteSubscription::connect_with_capacity(
        args.aeron_dir.as_deref(),
        &args.channel,
        args.stream_id,
        args.max_frame_len,
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
