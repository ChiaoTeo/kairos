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
use kairos_primitives::runtime::ActorId;
use kairos_primitives::{
    AccountId, ClientOrderId, Currency, ExecutionRouteId, FillId, Generation, InstrumentId,
    IntentId, LegId, MarketId, Money, OrderId, PlanId, Price, Quantity, RemoteOrderId, SegmentKey,
    Sequence, Symbol, UnixNanos,
};
pub use query::*;
pub(crate) use result::remote_status;
pub use result::*;
pub use risk::{
    ExecutionFundingRequirement, RiskAuthorizationContext, RiskCommandFailure, RiskCommandResult,
};
use serde::{Deserialize, Serialize};
pub use snapshot::*;

use crate::domain::{
    CompletionPolicy, ExecutionAttempt, ExecutionFill, ExecutionOrder, ExecutionOrderStatus,
    ExecutionPlan, FailurePolicy, HedgePolicy, IntentType, MakerExecutionPolicy, OrderCommitment,
    OrderSide, OrderType, RiskReservationEvidence, SplitOrderPolicy,
};
