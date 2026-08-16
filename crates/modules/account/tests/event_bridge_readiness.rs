use std::process::Command;

use rusteron_media_driver::testing::EmbeddedDriver;

#[test]
fn account_bridge_check_connects_to_real_media_driver() {
    let driver = EmbeddedDriver::launch().expect("launch embedded Aeron driver");
    let output = Command::new(env!("CARGO_BIN_EXE_kairos-account-event-bridge"))
        .args([
            "--aeron-dir",
            driver.dir(),
            "--aeron-channel",
            "aeron:ipc",
            "--stream-id",
            "21401",
            "--check",
        ])
        .output()
        .expect("start Account event bridge readiness process");
    assert!(
        output.status.success(),
        "Account event bridge readiness failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}
