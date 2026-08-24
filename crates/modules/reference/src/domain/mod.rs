//! Reference domain exchanges, errors, and catalog aggregate.

mod catalog;
mod entities;
mod error;

pub use catalog::*;
pub use entities::*;
pub use error::{ReferenceError, ReferenceResult};
