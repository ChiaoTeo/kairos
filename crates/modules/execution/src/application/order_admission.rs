/// Validates a concrete order against current dependency projections and
/// returns its durable resource commitment plus Risk authorization context.
pub trait ExecutionOrderAdmission: Send {
    fn dependency_watermarks(&self) -> crate::application::DependencyWatermarks {
        crate::application::DependencyWatermarks::default()
    }

    fn validate_order(
        &mut self,
        request: &crate::application::SubmitOrder,
        active_commitments: &[crate::domain::OrderCommitment],
    ) -> Result<crate::domain::OrderCommitment, String>;

    fn risk_authorization_context(
        &mut self,
        request: &crate::application::SubmitOrder,
    ) -> Result<crate::application::RiskAuthorizationContext, String> {
        let watermarks = self.dependency_watermarks();
        Ok(crate::application::RiskAuthorizationContext {
            account: watermarks
                .account
                .get(request.account_id.as_str())
                .cloned()
                .unwrap_or_default(),
            market: watermarks.market,
            market_is_fresh: true,
            available_margin: None,
        })
    }
}
