use super::CircuitState;
use kairos_primitives::UnixNanos;

pub fn blocks(state: &CircuitState, at_unix_nanos: UnixNanos) -> bool {
    state.blocks_at(at_unix_nanos)
}
