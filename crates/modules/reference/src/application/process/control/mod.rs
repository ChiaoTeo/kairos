//! Control-plane adapters and contract response mappings.

mod conflux;
mod diagnostics;
mod planning;
mod source;
mod status;

use super::super::{ReferenceApplication, ReferenceRpcActor};
use super::ReferenceTickTrigger;
