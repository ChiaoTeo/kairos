use std::process::Command;

use rusteron_media_driver::testing::EmbeddedDriver;

#[test]
fn market_bridge_check_connects_to_real_media_driver() {
    let driver = EmbeddedDriver::launch().expect("launch embedded Aeron driver");
    let output = Command::new(env!("CARGO_BIN_EXE_kairos-market-event-bridge"))
        .args([
            "--aeron-dir",
            driver.dir(),
            "--aeron-channel",
            "aeron:ipc",
            "--stream-id",
            "21301",
            "--check",
        ])
        .output()
        .expect("start Market event bridge readiness process");
    assert!(
        output.status.success(),
        "Market event bridge readiness failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}
