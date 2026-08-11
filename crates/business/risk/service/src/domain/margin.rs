use super::Amount;

pub fn is_available(available: Amount, required: Amount) -> bool {
    required.cmp_value(available).is_le()
}
