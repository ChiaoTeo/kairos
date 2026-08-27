use kairos_capital_contract::{
    CapitalEvent, CapitalPolicy, FlatbuffersCapitalEventWriter, FundingLocation,
};
use kairos_primitives::account::{AccountId, BrokerId, SegmentKey};
use kairos_primitives::reference::Currency;
use kairos_primitives::runtime::InstanceIdentity;
use kairos_primitives::time::{Generation, Sequence, UnixNanos};

fn main() {
    let mut writer = FlatbuffersCapitalEventWriter::new(
        "capital:fixture",
        InstanceIdentity::new("workspace", "launch", "instance").unwrap(),
    );
    writer
        .publish(&CapitalEvent::PolicyChanged {
            policy: CapitalPolicy {
                destination: FundingLocation {
                    broker: BrokerId::new("binance").unwrap(),
                    account_id: AccountId::new("account-1").unwrap(),
                    segment: SegmentKey::new("usd-m").unwrap(),
                    asset: Currency::new("USDT").unwrap(),
                },
                version: Generation::new(7),
                minimum: "10.25".parse().unwrap(),
                default_target: "20.5".parse().unwrap(),
                maximum: "30.75".parse().unwrap(),
                stress_buffer: "2".parse().unwrap(),
                minimum_movement: "1".parse().unwrap(),
                hysteresis: "0.5".parse().unwrap(),
                deficit_dwell_nanos: 5.into(),
                cooldown_nanos: 6.into(),
                max_fact_age_nanos: 7.into(),
            },
            event_sequence: Sequence::new(11),
            occurred_at: UnixNanos::new(12),
        })
        .unwrap();
    for byte in writer.last_payload.unwrap() {
        print!("{byte:02x}");
    }
}
