use kairos_primitives::time::UnixNanos;

use super::CircuitState;

pub fn blocks(state: &CircuitState, at_unix_nanos: UnixNanos) -> bool {
    state.blocks_at(at_unix_nanos)
}
