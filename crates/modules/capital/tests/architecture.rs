use std::fs;
use std::path::PathBuf;

/// This is the one Capital source-shape rule not yet expressible through the
/// dependency checks or existing behavior tests. Keep its scope to the Actor
/// shutdown hook until a runtime shutdown test can observe participant calls.
#[test]
fn capital_shutdown_reconciles_but_never_compensates_or_resubmits() {
    let crate_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let actor = fs::read_to_string(crate_root.join("src/application/process/conflux.rs")).unwrap();
    let start = actor.find("async fn stopping").unwrap();
    let end = actor[start..].find("impl<C> CapitalProcess").unwrap() + start;
    let handler = &actor[start..end];
    assert!(handler.contains("reconcile_capital_plan"));
    assert!(handler.contains("record_recovery_required"));
    assert!(!handler.contains("execute_capital_plan"));
    assert!(!handler.contains("submit_"));
    assert!(!handler.contains("compensate"));
}
