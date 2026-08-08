use crate::application::ExecutionSnapshot;

/// Composition-owned safety boundary for a strategy intent execution.
/// Implementations may aggregate Account, Risk, Market, Reference and System
/// clients, while Execution remains the owner of intent/order state.
pub trait ExecutionPreflight: Send {
    /// Resolve a strategy intent into the concrete per-account orders that
    /// will be submitted. The implementation is assembled in composition and
    /// may consult Account, Risk, Market, and Reference application APIs.
    fn plan_intent(
        &mut self,
        intent: &crate::application::ExecuteStrategyIntent,
    ) -> Result<Vec<crate::application::SubmitOrder>, String>;
    fn validate_order(&mut self, request: &crate::application::SubmitOrder) -> Result<(), String>;
    fn prepare_order(&mut self, _request: &crate::application::SubmitOrder) -> Result<(), String> {
        Ok(())
    }
    fn publish_order(&mut self, _order: &crate::domain::ExecutionOrder) -> Result<(), String> {
        Ok(())
    }
    fn publish_fill(&mut self, _fill: &crate::domain::ExecutionFill) -> Result<(), String> {
        Ok(())
    }
    fn reserve_order(&mut self, request: &crate::application::SubmitOrder) -> Result<(), String>;
    fn resize_order(
        &mut self,
        _order_id: &str,
        _remaining_quantity_mantissa: i64,
        _quantity_scale: u8,
    ) -> Result<(), String> {
        Ok(())
    }
    fn release_order(&mut self, order_id: &str) -> Result<(), String>;
    fn consume_order(&mut self, order_id: &str) -> Result<(), String>;
}

pub trait ExecutionStateStore: Send {
    fn load(&mut self) -> Result<Option<ExecutionSnapshot>, String>;
    fn save(&mut self, snapshot: &ExecutionSnapshot) -> Result<(), String>;
}
