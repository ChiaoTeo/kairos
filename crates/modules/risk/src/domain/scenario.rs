use super::Amount;

pub fn loss_exceeds(limit: Amount, observed_loss: Amount) -> bool {
    observed_loss.cmp_value(limit).is_gt()
}
