use kairos_primitives::runtime::InstanceIdentity;

use crate::domain::events::MarketEvent;

pub(crate) fn encode_event(
    actor_id: &str,
    producer_incarnation: u64,
    identity: &InstanceIdentity,
    sequence: u64,
    event: &MarketEvent,
) -> Result<Vec<u8>, String> {
    super::encoding::encode_contract_event(
        actor_id,
        producer_incarnation,
        identity,
        sequence,
        event,
    )
}
