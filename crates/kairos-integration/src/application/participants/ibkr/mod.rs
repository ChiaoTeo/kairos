//! Interactive Brokers participant facade.

mod connection;

pub use connection::IbkrConnectionConfig;

pub mod blocking {
    pub use super::connection::{
        blocking_account as account, blocking_account_stream as account_stream,
        blocking_execution_stream as execution_stream, blocking_order_entry as order_entry,
    };
}
