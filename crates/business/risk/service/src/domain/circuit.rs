use super::CircuitState;
use kairos_domain_types::UnixNanos;

pub fn blocks(state: &CircuitState, at_unix_nanos: UnixNanos) -> bool {
    state.blocks_at(at_unix_nanos)
}
