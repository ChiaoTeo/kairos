//! Execution snapshots, current views, and dependency watermarks.

use super::*;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ExecutionSnapshot {
    pub actor_id: ActorId,
    pub generation: Generation,
    pub event_sequence: Sequence,
    pub orders: Vec<ExecutionOrder>,
    #[serde(default)]
    pub events: Vec<ExecutionEvent>,
    #[serde(default)]
    pub fills: Vec<ExecutionFill>,
    #[serde(default)]
    pub algorithm_runs: Vec<AlgorithmRun>,
    #[serde(default)]
    pub commitments: Vec<OrderCommitment>,
    #[serde(default)]
    pub risk_reservations: Vec<RiskReservationEvidence>,
    #[serde(default)]
    pub intents: Vec<IntentState>,
    #[serde(default)]
    pub intent_events: Vec<IntentEvent>,
    #[serde(default)]
    pub intent_idempotency: BTreeMap<String, String>,
    #[serde(default)]
    pub unknown_remote_orders: Vec<UnknownRemoteOrder>,
    #[serde(default)]
    pub exchange_event_watermark_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ExecutionCurrentView {
    pub generation: Generation,
    pub event_sequence: Sequence,
    pub orders: Vec<ExecutionOrder>,
    pub commitments: Vec<OrderCommitment>,
    pub risk_reservations: Vec<RiskReservationEvidence>,
    pub intents: Vec<IntentState>,
    pub events: Vec<ExecutionEvent>,
    pub intent_events: Vec<IntentEvent>,
    pub fills: Vec<ExecutionFill>,
    pub algorithm_runs: Vec<AlgorithmRun>,
    pub unknown_remote_orders: Vec<UnknownRemoteOrder>,
    pub exchange_event_watermark_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct SnapshotWatermark {
    pub generation: Generation,
    pub event_sequence: Sequence,
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct DependencyWatermarks {
    #[serde(default)]
    pub account: BTreeMap<String, SnapshotWatermark>,
    pub market: Option<SnapshotWatermark>,
    pub reference: Option<SnapshotWatermark>,
    pub risk: Option<SnapshotWatermark>,
}
