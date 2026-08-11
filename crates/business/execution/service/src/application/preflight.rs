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
    fn latest_quote(
        &mut self,
        _instrument_id: &str,
        _market_id: Option<&str>,
    ) -> Result<Option<crate::application::QuoteObservation>, String> {
        Ok(None)
    }
    fn dependency_watermarks(&self) -> crate::application::DependencyWatermarks {
        crate::application::DependencyWatermarks::default()
    }
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
