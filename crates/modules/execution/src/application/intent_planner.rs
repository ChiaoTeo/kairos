/// Resolves an Execution-owned intent into concrete child orders and supplies
/// quote observations needed to refresh maker plans.
pub trait ExecutionIntentPlanner: Send {
    fn advance_time(&mut self, _event_time_unix_nanos: u64) -> Result<(), String> {
        Ok(())
    }

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
}
