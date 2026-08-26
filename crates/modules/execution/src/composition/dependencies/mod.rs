//! Assembly of concrete cross-process dependency and Risk services.

use crate::services::risk::SimulatedRiskBehavior;

/// Configure Execution's concrete cross-process facts and Risk services.
/// Concrete readers and workers remain private to the module.
pub fn configure_execution_dependencies(
    application: &mut crate::application::ExecutionApplication,
    system: &mut kairos_conflux::ConfluxSystem,
    manifest: impl AsRef<std::path::Path>,
    reference_snapshot: Option<kairos_reference_contract::ExecutionReferenceSnapshot>,
    backtest: bool,
    capacity: usize,
) -> Result<(), String> {
    use crate::services::dependencies::{
        ExecutionOrderAdmissionService, QueuedExecutionIntentPlanner,
        QueuedExecutionOrderAdmission, SocketExecutionIntentPlanner, SocketExecutionOrderAdmission,
    };

    let manifest = manifest.as_ref();
    let mut intent_planner = SocketExecutionIntentPlanner::from_manifest_with_reference_snapshot(
        system,
        manifest,
        reference_snapshot.clone(),
    )?;
    let mut order_admission = SocketExecutionOrderAdmission::from_manifest_with_reference_snapshot(
        system,
        manifest,
        reference_snapshot,
    )?;
    if backtest {
        intent_planner = intent_planner.without_market_snapshot();
        order_admission = order_admission
            .without_market_snapshot()
            .with_backtest_reservation_window()
            .allow_backtest_without_reference_state(true)
            .allow_backtest_without_account_state(true);
    }
    let risk_reservations = order_admission.risk_reservations_adapter()?;
    application.attach_intent_planner(QueuedExecutionIntentPlanner::start(
        intent_planner,
        capacity,
    )?)?;
    application.attach_order_admission(ExecutionOrderAdmissionService::live(
        QueuedExecutionOrderAdmission::start(order_admission, capacity)?,
    ));
    application.attach_risk_reservations(
        crate::services::risk::QueuedExecutionRiskReservations::start(risk_reservations, capacity)?,
    );
    Ok(())
}

pub fn configure_simulated_risk(
    application: &mut crate::application::ExecutionApplication,
    behavior: SimulatedRiskBehavior,
    capacity: usize,
) -> Result<(), String> {
    application.attach_order_admission(
        crate::services::dependencies::ExecutionOrderAdmissionService::simulated(),
    );
    application.attach_risk_reservations(
        crate::services::risk::QueuedExecutionRiskReservations::simulated(behavior, capacity)?,
    );
    Ok(())
}
