//! Execution application commands, results, views, and errors.

mod command;
mod error;
mod event;
mod query;
mod result;
mod risk;
mod snapshot;

use std::collections::BTreeMap;

pub use command::*;
pub use error::*;
pub use event::*;
use kairos_primitives::account::{AccountId, SegmentKey};
use kairos_primitives::decimal::{Money, Price, Quantity};
use kairos_primitives::execution::{
    ClientOrderId, ExecutionRouteId, FillId, IntentId, LegId, OrderId, PlanId,
};
use kairos_primitives::integration::RemoteOrderId;
use kairos_primitives::reference::{Currency, InstrumentId, MarketId, Symbol};
use kairos_primitives::runtime::ActorId;
use kairos_primitives::time::{DurationNanos, Generation, Sequence, UnixNanos};
pub use query::*;
pub(crate) use result::remote_status;
pub use result::*;
pub use risk::{
    ExecutionFundingRequirement, RiskAuthorizationContext, RiskCommandFailure, RiskCommandResult,
};
use serde::{Deserialize, Serialize};
pub use snapshot::*;

use crate::domain::{
    AlgorithmRun, CompletionPolicy, ExecutionAlgorithmPolicy, ExecutionAttempt,
    ExecutionBenchmarkKind, ExecutionFill, ExecutionOrder, ExecutionOrderStatus, ExecutionPlan,
    FailurePolicy, IntentType, MakerExecutionPolicy, OrderCommitment, OrderSide, OrderType,
    RiskReservationEvidence, SplitOrderPolicy,
};
