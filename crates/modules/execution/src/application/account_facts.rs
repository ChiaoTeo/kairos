/// Outbound Account fact boundary owned by Execution application orchestration.
///
/// This is deliberately separate from admission and Risk reservation. The
/// implementation publishes already-accepted Execution facts; it does not
/// decide whether an order may execute or mutate Execution lifecycle state.
pub trait ExecutionAccountFacts: Send {
    fn publish_order(&mut self, order: &crate::domain::ExecutionOrder) -> Result<(), String>;

    fn publish_fill(
        &mut self,
        fill: &crate::domain::ExecutionFill,
        order: &crate::domain::ExecutionOrder,
        commitment: &crate::domain::OrderCommitment,
    ) -> Result<(), String>;
}
