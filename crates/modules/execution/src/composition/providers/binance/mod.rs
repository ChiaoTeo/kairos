//! Binance-native connection construction selected by Execution composition.

mod common;
mod futures;
mod options;
mod spot;

pub(in crate::composition) use futures::private_connection as futures_private_connection;
pub(in crate::composition) use options::private_connection as options_private_connection;
pub(in crate::composition) use spot::{
    channel_config as spot_channel_config, private_connection as spot_private_connection,
    private_connection_from_provider as spot_private_connection_from_provider,
    provider_connection as spot_provider_connection,
    same_provider_context as same_spot_provider_context,
};
