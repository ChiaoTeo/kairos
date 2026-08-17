use kairos_protocol::InstanceIdentity;

use crate::domain::events::MarketEvent;

pub(crate) fn encode_event(
    actor_id: &str,
    identity: &InstanceIdentity,
    sequence: u64,
    event: &MarketEvent,
) -> Result<Vec<u8>, String> {
    super::encoding::encode_contract_event(actor_id, identity, sequence, event)
}
