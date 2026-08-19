use kairos_primitives::UnixNanos;

use super::CircuitState;

pub fn blocks(state: &CircuitState, at_unix_nanos: UnixNanos) -> bool {
    state.blocks_at(at_unix_nanos)
}
