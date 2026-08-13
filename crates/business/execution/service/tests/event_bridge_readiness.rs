use std::process::Command;

use rusteron_media_driver::testing::EmbeddedDriver;

#[test]
fn execution_bridge_check_connects_to_real_media_driver() {
    let driver = EmbeddedDriver::launch().expect("launch embedded Aeron driver");
    let output = Command::new(env!("CARGO_BIN_EXE_kairos-execution-event-bridge"))
        .args([
            "--aeron-dir",
            driver.dir(),
            "--aeron-channel",
            "aeron:ipc",
            "--stream-id",
            "21501",
            "--check",
        ])
        .output()
        .expect("start Execution event bridge readiness process");
    assert!(
        output.status.success(),
        "Execution event bridge readiness failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}
