//! OKX-native capability construction.

mod trading;

pub(in crate::composition) use trading::{
    private_connection, private_connection_from_provider, provider_connection,
    same_provider_context, trading_shape,
};
