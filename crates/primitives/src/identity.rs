//! Transitional identities whose business governor still needs to be resolved.
//!
//! Do not add new types here. Each type must move to an owner namespace when
//! its contract slice is migrated.

use crate::text::text_type;

text_type!(StrategyId);
text_type!(StrategyDecisionId);
text_type!(RequestId);
text_type!(IdempotencyKey);
