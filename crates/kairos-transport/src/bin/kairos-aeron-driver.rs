use rusteron_media_driver::{AeronDriver, AeronDriverContext, IntoCString};
use std::env;
use std::fs;
use std::path::PathBuf;
use std::time::Duration;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut arguments = env::args().skip(1);
    let mut aeron_dir = None;
    let mut health_file: Option<PathBuf> = None;
    while let Some(argument) = arguments.next() {
        match argument.as_str() {
            "--aeron-dir" => aeron_dir = arguments.next(),
            "--health-file" => health_file = arguments.next().map(PathBuf::from),
            "--help" | "-h" => {
                println!("Usage: kairos-aeron-driver [--aeron-dir <path>] [--health-file <path>]");
                return Ok(());
            }
            other => return Err(format!("unknown argument: {other}").into()),
        }
    }

    let context = AeronDriverContext::new()?;
    if let Some(directory) = aeron_dir {
        context.set_dir(&directory.into_c_string())?;
    }
    let directory = context.get_dir().to_string();
    let driver = AeronDriver::launch_embedded_guard(context, true);
    if let Some(path) = &health_file {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::write(
            path,
            format!("{{\"status\":\"ready\",\"pid\":{}}}\n", std::process::id()),
        )?;
    }
    println!("kairos-aeron-driver ready dir={directory}");
    while !driver.is_finished() {
        std::thread::sleep(Duration::from_secs(1));
    }
    if let Some(path) = &health_file {
        let _ = fs::remove_file(path);
    }
    driver.join()?;
    Ok(())
}
