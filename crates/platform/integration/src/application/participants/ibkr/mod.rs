//! Interactive Brokers participant facade.

mod connection;

pub use connection::{
    IbkrAccountEvents, IbkrAccountRead, IbkrConnection, IbkrConnectionConfig, IbkrOrderEntry,
    IbkrOrderEvents, IbkrOrderQuery,
};

pub mod blocking {
    pub use super::connection::{
        blocking_account as account, blocking_account_stream as account_stream,
    };
}
