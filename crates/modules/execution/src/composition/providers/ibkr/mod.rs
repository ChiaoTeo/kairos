//! IBKR-native capability construction.

mod trading;

pub(in crate::composition) use trading::{compose_async_execution, connection};
