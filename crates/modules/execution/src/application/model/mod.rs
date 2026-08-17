//! Execution application commands, results, views, and errors.

mod command;
mod error;
mod event;
mod query;
mod result;
mod risk;
mod snapshot;

pub use command::*;
pub use error::*;
pub use event::*;
pub use query::*;
pub(crate) use result::remote_status;
pub use result::*;
pub use risk::{RiskAuthorizationContext, RiskCommandFailure, RiskCommandResult};
pub use snapshot::*;

use kairos_primitives::{
    AccountId, ActorId, ClientOrderId, Currency, ExecutionAccessId, FillId, Generation,
    InstrumentId, IntentId, LegId, MarketId, Money, OrderId, PlanId, Price, Quantity,
    RemoteOrderId, SegmentKey, Sequence, Symbol, UnixNanos,
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

use crate::domain::{
    CompletionPolicy, ExecutionFill, ExecutionOrder, ExecutionOrderStatus, ExecutionPlan,
    FailurePolicy, HedgePolicy, IntentType, MakerExecutionPolicy, OrderCommitment, OrderSide,
    OrderType, RiskReservationEvidence, SplitOrderPolicy,
};
