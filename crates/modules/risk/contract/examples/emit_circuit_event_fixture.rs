use kairos_primitives::account::AccountId;
use kairos_primitives::runtime::{InstanceIdentity, StrategyId};
use kairos_primitives::time::{Sequence, UnixNanos};
use kairos_risk_contract::{CircuitScope, CircuitState, FlatbuffersRiskEventWriter, RiskEvent};

fn main() {
    let mut writer = FlatbuffersRiskEventWriter::new_with_identity(
        kairos_primitives::runtime::ActorId::new("risk:fixture").unwrap(),
        InstanceIdentity::new("workspace", "launch", "instance").unwrap(),
    );
    writer
        .publish(&RiskEvent::CircuitChanged {
            circuit: CircuitState {
                scope: CircuitScope {
                    account_id: Some(AccountId::new("account-1").unwrap()),
                    strategy_id: Some(StrategyId::new("strategy-1").unwrap()),
                    exchange_id: None,
                },
                open: true,
                opened_at_unix_nanos: Some(UnixNanos::new(12)),
                reset_at_unix_nanos: None,
                reason: "fixture circuit".to_owned(),
            },
            event_sequence: Sequence::new(13),
        })
        .unwrap();
    for byte in writer.last_payload.unwrap() {
        print!("{byte:02x}");
    }
}
