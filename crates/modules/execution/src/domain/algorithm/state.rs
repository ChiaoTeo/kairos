use std::fmt;

use kairos_primitives::decimal::{Quantity, Ratio};
use kairos_primitives::execution::{ExecutionRouteId, IntentId, LegId, OrderId};
use kairos_primitives::time::{DurationNanos, UnixNanos};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
#[serde(transparent)]
pub struct AlgorithmRunId(String);

impl AlgorithmRunId {
    pub fn new(value: impl Into<String>) -> Result<Self, String> {
        let value = value.into();
        if value.trim().is_empty() {
            return Err("algorithm run id is required".into());
        }
        Ok(Self(value))
    }

    pub fn for_intent(intent_id: &IntentId) -> Self {
        Self(format!("{}:algorithm:1", intent_id.as_str()))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for AlgorithmRunId {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MakerTakerHedgeSpec {
    pub leader_leg_id: LegId,
    pub hedge_leg_id: LegId,
    pub hedge_ratio: Ratio,
    pub contract_multiplier: Ratio,
    pub max_unhedged_quantity: Quantity,
    #[serde(default)]
    pub max_unhedged_duration: Option<DurationNanos>,
    #[serde(default)]
    pub fallback_execution_route_ids: Vec<ExecutionRouteId>,
}

impl MakerTakerHedgeSpec {
    pub fn validate(&self) -> Result<(), String> {
        if self.leader_leg_id == self.hedge_leg_id {
            return Err("maker-taker leader and hedge legs must differ".into());
        }
        if self
            .max_unhedged_duration
            .is_some_and(|duration| duration.get() == 0)
        {
            return Err("maker-taker maximum unhedged duration must be positive".into());
        }
        let mut routes = std::collections::BTreeSet::new();
        if self
            .fallback_execution_route_ids
            .iter()
            .any(|route_id| !routes.insert(route_id))
        {
            return Err("maker-taker fallback routes must be unique".into());
        }
        Ok(())
    }

    pub fn exposure_deadline(
        &self,
        exposure: &NormalizedExposureLedger,
    ) -> Result<Option<UnixNanos>, String> {
        self.max_unhedged_duration
            .zip(exposure.unhedged_since)
            .map(|(duration, since)| {
                since
                    .get()
                    .checked_add(duration.get())
                    .map(UnixNanos::new)
                    .ok_or_else(|| "maker-taker exposure deadline overflow".to_string())
            })
            .transpose()
    }

    pub fn hedge_due(
        &self,
        exposure: &NormalizedExposureLedger,
        business_time: UnixNanos,
    ) -> Result<bool, String> {
        Ok(
            exposure.unhedged_filled_quantity > self.max_unhedged_quantity
                || (!exposure.unhedged_filled_quantity.is_zero()
                    && self
                        .exposure_deadline(exposure)?
                        .is_some_and(|deadline| business_time >= deadline)),
        )
    }

    pub fn required_hedge_quantity(&self, leader_filled: Quantity) -> Result<Quantity, String> {
        let required = self
            .hedge_ratio
            .apply_to_nonnegative(leader_filled.mantissa())
            .map_err(|error| error.to_string())?;
        let required = self
            .contract_multiplier
            .apply_to_nonnegative(required)
            .map_err(|error| error.to_string())?;
        Quantity::new(required, leader_filled.scale()).map_err(|error| error.to_string())
    }

    pub fn leader_quantity_for_hedge_exposure(
        &self,
        hedge_exposure: Quantity,
    ) -> Result<Quantity, String> {
        let numerator = u128::from(self.hedge_ratio.numerator())
            .checked_mul(u128::from(self.contract_multiplier.numerator()))
            .ok_or_else(|| "maker-taker ratio overflow".to_string())?;
        let denominator = u128::from(self.hedge_ratio.denominator())
            .checked_mul(u128::from(self.contract_multiplier.denominator()))
            .ok_or_else(|| "maker-taker ratio overflow".to_string())?;
        let exposure = u128::try_from(hedge_exposure.mantissa())
            .map_err(|_| "hedge exposure cannot be negative".to_string())?;
        let scaled = exposure
            .checked_mul(denominator)
            .ok_or_else(|| "maker-taker exposure overflow".to_string())?;
        let leader = scaled
            .checked_add(numerator.saturating_sub(1))
            .ok_or_else(|| "maker-taker exposure overflow".to_string())?
            / numerator;
        Quantity::new(
            i64::try_from(leader).map_err(|_| "maker-taker exposure overflow".to_string())?,
            hedge_exposure.scale(),
        )
        .map_err(|error| error.to_string())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct TwapSpec {
    pub leg_id: LegId,
    pub start_at: UnixNanos,
    pub slice_interval: DurationNanos,
    pub slice_count: u32,
}

impl TwapSpec {
    pub fn validate(&self) -> Result<(), String> {
        if self.slice_interval.get() == 0 || self.slice_count < 2 {
            return Err("TWAP requires at least two slices and a positive interval".into());
        }
        self.due_at(self.slice_count.saturating_sub(1))?;
        Ok(())
    }

    pub fn due_at(&self, slice_index: u32) -> Result<UnixNanos, String> {
        if slice_index >= self.slice_count {
            return Err("TWAP slice index exceeds the configured schedule".into());
        }
        self.slice_interval
            .get()
            .checked_mul(u64::from(slice_index))
            .and_then(|offset| self.start_at.get().checked_add(offset))
            .map(UnixNanos::new)
            .ok_or_else(|| "TWAP schedule overflows business time".to_string())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ExecutionAlgorithmSpec {
    Immediate,
    Twap(TwapSpec),
    MakerTakerHedge(MakerTakerHedgeSpec),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum AlgorithmRunStatus {
    Planned,
    Running,
    Waiting,
    Completed,
    Unwound,
    Failed,
    ReconciliationRequired,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum AlgorithmLegRole {
    Immediate,
    Twap,
    LeaderMaker,
    HedgeTaker,
    Unwind,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum AlgorithmLegLifecycle {
    Dormant,
    Ready,
    Active,
    Completed,
    Failed,
    ReconciliationRequired,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AlgorithmLegState {
    pub leg_id: LegId,
    pub role: AlgorithmLegRole,
    pub lifecycle: AlgorithmLegLifecycle,
    pub target_quantity: Quantity,
    /// Quantity that may still fill on non-terminal or indeterminate orders.
    pub committed_quantity: Quantity,
    pub filled_quantity: Quantity,
}

impl AlgorithmLegState {
    pub fn immediate(leg_id: LegId, target_quantity: Quantity) -> Result<Self, String> {
        if target_quantity.is_zero() {
            return Err("algorithm leg target quantity must be positive".into());
        }
        Ok(Self {
            leg_id,
            role: AlgorithmLegRole::Immediate,
            lifecycle: AlgorithmLegLifecycle::Ready,
            target_quantity,
            committed_quantity: Quantity::ZERO,
            filled_quantity: Quantity::ZERO,
        })
    }

    pub fn remaining_uncommitted(&self) -> Result<Quantity, String> {
        self.target_quantity
            .checked_sub(self.filled_quantity)
            .and_then(|remaining| remaining.checked_sub(self.committed_quantity))
            .map_err(|error| error.to_string())
    }

    pub fn validate(&self) -> Result<(), String> {
        let accounted = self
            .filled_quantity
            .checked_add(self.committed_quantity)
            .map_err(|error| error.to_string())?;
        if accounted > self.target_quantity {
            return Err(format!(
                "algorithm leg {} accounts for more than its target",
                self.leg_id
            ));
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum AlgorithmActionStatus {
    Pending,
    Completed,
    Failed,
    Indeterminate,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum AlgorithmExecutionStyle {
    Immediate,
    TwapSlice,
    MakerPostOnly,
    TakerImmediate,
    UnwindImmediate,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum AlgorithmActionKind {
    SubmitChild {
        order_id: OrderId,
        leg_id: LegId,
        quantity: Quantity,
        execution_style: AlgorithmExecutionStyle,
        #[serde(default)]
        execution_route_id: Option<ExecutionRouteId>,
    },
    Complete,
    RequireReconciliation {
        reason: String,
    },
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AlgorithmAction {
    pub action_id: String,
    pub decision_sequence: u64,
    pub status: AlgorithmActionStatus,
    pub kind: AlgorithmActionKind,
}

/// Exposure expressed in hedge-leg quantity units. Filled exposure and
/// still-live hedge commitments are deliberately separate so a decision does
/// not submit a duplicate hedge while an earlier hedge may still fill.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct NormalizedExposureLedger {
    /// Gross leader fills before any emergency unwind.
    pub leader_filled_quantity: Quantity,
    #[serde(default)]
    pub unwind_filled_quantity: Quantity,
    #[serde(default)]
    pub unwind_committed_quantity: Quantity,
    #[serde(default)]
    pub net_leader_filled_quantity: Quantity,
    pub required_hedge_quantity: Quantity,
    pub hedge_filled_quantity: Quantity,
    pub hedge_committed_quantity: Quantity,
    pub unhedged_filled_quantity: Quantity,
    pub unhedged_after_commitment: Quantity,
    #[serde(default)]
    pub unhedged_since: Option<UnixNanos>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct AlgorithmRun {
    pub algorithm_run_id: AlgorithmRunId,
    pub algorithm_version: u32,
    pub intent_id: IntentId,
    pub spec: ExecutionAlgorithmSpec,
    pub status: AlgorithmRunStatus,
    pub decision_sequence: u64,
    pub last_decision_at: Option<UnixNanos>,
    pub next_wake_at: Option<UnixNanos>,
    #[serde(default)]
    pub exposure: Option<NormalizedExposureLedger>,
    pub legs: Vec<AlgorithmLegState>,
    pub actions: Vec<AlgorithmAction>,
}

impl AlgorithmRun {
    pub fn immediate(
        intent_id: IntentId,
        legs: impl IntoIterator<Item = (LegId, Quantity)>,
    ) -> Result<Self, String> {
        let legs = legs
            .into_iter()
            .map(|(leg_id, target)| AlgorithmLegState::immediate(leg_id, target))
            .collect::<Result<Vec<_>, _>>()?;
        if legs.is_empty() {
            return Err("algorithm run requires at least one leg".into());
        }
        let run = Self {
            algorithm_run_id: AlgorithmRunId::for_intent(&intent_id),
            algorithm_version: 1,
            intent_id,
            spec: ExecutionAlgorithmSpec::Immediate,
            status: AlgorithmRunStatus::Planned,
            decision_sequence: 0,
            last_decision_at: None,
            next_wake_at: None,
            exposure: None,
            legs,
            actions: Vec::new(),
        };
        run.validate()?;
        Ok(run)
    }

    pub fn completed(intent_id: IntentId) -> Self {
        Self {
            algorithm_run_id: AlgorithmRunId::for_intent(&intent_id),
            algorithm_version: 1,
            intent_id,
            spec: ExecutionAlgorithmSpec::Immediate,
            status: AlgorithmRunStatus::Completed,
            decision_sequence: 0,
            last_decision_at: None,
            next_wake_at: None,
            exposure: None,
            legs: Vec::new(),
            actions: Vec::new(),
        }
    }

    pub fn twap(
        intent_id: IntentId,
        spec: TwapSpec,
        target_quantity: Quantity,
    ) -> Result<Self, String> {
        spec.validate()?;
        if target_quantity.is_zero() {
            return Err("TWAP target quantity must be positive".into());
        }
        let run = Self {
            algorithm_run_id: AlgorithmRunId::for_intent(&intent_id),
            algorithm_version: 1,
            intent_id,
            spec: ExecutionAlgorithmSpec::Twap(spec.clone()),
            status: AlgorithmRunStatus::Planned,
            decision_sequence: 0,
            last_decision_at: None,
            next_wake_at: Some(spec.start_at),
            exposure: None,
            legs: vec![AlgorithmLegState {
                leg_id: spec.leg_id,
                role: AlgorithmLegRole::Twap,
                lifecycle: AlgorithmLegLifecycle::Ready,
                target_quantity,
                committed_quantity: Quantity::ZERO,
                filled_quantity: Quantity::ZERO,
            }],
            actions: Vec::new(),
        };
        run.validate()?;
        Ok(run)
    }

    pub fn maker_taker_hedge(
        intent_id: IntentId,
        spec: MakerTakerHedgeSpec,
        leader_target_quantity: Quantity,
        hedge_target_quantity: Quantity,
    ) -> Result<Self, String> {
        spec.validate()?;
        let leader = AlgorithmLegState {
            leg_id: spec.leader_leg_id.clone(),
            role: AlgorithmLegRole::LeaderMaker,
            lifecycle: AlgorithmLegLifecycle::Ready,
            target_quantity: leader_target_quantity,
            committed_quantity: Quantity::ZERO,
            filled_quantity: Quantity::ZERO,
        };
        let hedge = AlgorithmLegState {
            leg_id: spec.hedge_leg_id.clone(),
            role: AlgorithmLegRole::HedgeTaker,
            lifecycle: AlgorithmLegLifecycle::Dormant,
            target_quantity: hedge_target_quantity,
            committed_quantity: Quantity::ZERO,
            filled_quantity: Quantity::ZERO,
        };
        if leader_target_quantity.is_zero() || hedge_target_quantity.is_zero() {
            return Err("maker-taker leg targets must be positive".into());
        }
        let run = Self {
            algorithm_run_id: AlgorithmRunId::for_intent(&intent_id),
            algorithm_version: 2,
            intent_id,
            spec: ExecutionAlgorithmSpec::MakerTakerHedge(spec),
            status: AlgorithmRunStatus::Planned,
            decision_sequence: 0,
            last_decision_at: None,
            next_wake_at: None,
            exposure: Some(NormalizedExposureLedger {
                leader_filled_quantity: Quantity::ZERO,
                unwind_filled_quantity: Quantity::ZERO,
                unwind_committed_quantity: Quantity::ZERO,
                net_leader_filled_quantity: Quantity::ZERO,
                required_hedge_quantity: Quantity::ZERO,
                hedge_filled_quantity: Quantity::ZERO,
                hedge_committed_quantity: Quantity::ZERO,
                unhedged_filled_quantity: Quantity::ZERO,
                unhedged_after_commitment: Quantity::ZERO,
                unhedged_since: None,
            }),
            legs: vec![leader, hedge],
            actions: Vec::new(),
        };
        run.validate()?;
        Ok(run)
    }

    pub fn validate(&self) -> Result<(), String> {
        if self.algorithm_version == 0 {
            return Err("algorithm version must be positive".into());
        }
        let mut leg_ids = std::collections::BTreeSet::new();
        for leg in &self.legs {
            if !leg_ids.insert(leg.leg_id.clone()) {
                return Err(format!("duplicate algorithm leg: {}", leg.leg_id));
            }
            leg.validate()?;
        }
        if let ExecutionAlgorithmSpec::MakerTakerHedge(spec) = &self.spec {
            spec.validate()?;
            let leader = self
                .legs
                .iter()
                .find(|leg| leg.leg_id == spec.leader_leg_id)
                .ok_or_else(|| "maker-taker leader leg is missing".to_string())?;
            let hedge = self
                .legs
                .iter()
                .find(|leg| leg.leg_id == spec.hedge_leg_id)
                .ok_or_else(|| "maker-taker hedge leg is missing".to_string())?;
            if leader.role != AlgorithmLegRole::LeaderMaker
                || hedge.role != AlgorithmLegRole::HedgeTaker
            {
                return Err("maker-taker leg roles do not match the algorithm spec".into());
            }
            if self.exposure.is_none() {
                return Err("maker-taker run requires an exposure ledger".into());
            }
        }
        if let ExecutionAlgorithmSpec::Twap(spec) = &self.spec {
            spec.validate()?;
            if self.legs.len() != 1
                || self.legs[0].leg_id != spec.leg_id
                || self.legs[0].role != AlgorithmLegRole::Twap
                || self.exposure.is_some()
            {
                return Err("TWAP run does not match its single scheduled leg".into());
            }
            let submit_count = self
                .actions
                .iter()
                .filter(|action| {
                    matches!(
                        action.kind,
                        AlgorithmActionKind::SubmitChild {
                            execution_style: AlgorithmExecutionStyle::TwapSlice,
                            ..
                        }
                    )
                })
                .count();
            if submit_count > spec.slice_count as usize {
                return Err("TWAP run contains more slices than its schedule".into());
            }
        }
        let mut action_ids = std::collections::BTreeSet::new();
        for action in &self.actions {
            if !action_ids.insert(action.action_id.as_str()) {
                return Err(format!("duplicate algorithm action: {}", action.action_id));
            }
            if action.decision_sequence > self.decision_sequence {
                return Err("algorithm action references a future decision".into());
            }
        }
        Ok(())
    }

    pub fn pending_actions(&self) -> impl Iterator<Item = &AlgorithmAction> {
        self.actions
            .iter()
            .filter(|action| action.status == AlgorithmActionStatus::Pending)
    }
}
