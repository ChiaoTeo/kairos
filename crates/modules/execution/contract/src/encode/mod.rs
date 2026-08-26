mod metadata;
pub use metadata::{EncodeContext, event_metadata, view_metadata};

use crate::{ContractResult, ExecutionViewKey};

/// Business-owned implementations provide the concrete domain-to-wire mapping.
/// The contract crate owns the roots and transport semantics, not Execution's
/// mutable application model.
pub trait IntentEncoder {
    fn encode_intent_accepted(&self, context: &EncodeContext) -> ContractResult<Vec<u8>>;
    fn encode_intent_rejected(&self, context: &EncodeContext) -> ContractResult<Vec<u8>>;
}

pub trait PlanEncoder {
    fn encode_plan_created(&self, context: &EncodeContext) -> ContractResult<Vec<u8>>;
}

pub trait OrderEncoder {
    fn encode_order_submitted(&self, context: &EncodeContext) -> ContractResult<Vec<u8>>;
    fn encode_order_accepted(&self, context: &EncodeContext) -> ContractResult<Vec<u8>>;
    fn encode_order_rejected(&self, context: &EncodeContext) -> ContractResult<Vec<u8>>;
    fn encode_order_canceled(&self, context: &EncodeContext) -> ContractResult<Vec<u8>>;
    fn encode_order_expired(&self, context: &EncodeContext) -> ContractResult<Vec<u8>>;
}

pub trait FillEncoder {
    fn encode_fill_recorded(&self, context: &EncodeContext) -> ContractResult<Vec<u8>>;
}

pub trait CurrentExecutionViewEncoder {
    fn encode_current_execution(
        &self,
        context: &EncodeContext,
        key: &ExecutionViewKey,
    ) -> ContractResult<Vec<u8>>;
}
