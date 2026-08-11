use super::Amount;

/// Projects an account exposure with a proposed order delta without owning
/// the account position itself.
pub fn project(current: Amount, delta: Amount) -> Result<Amount, String> {
    current.checked_add(delta)
}
