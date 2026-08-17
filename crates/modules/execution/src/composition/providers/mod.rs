//! Provider-specific concrete connection construction.

mod binance;
mod ibkr;
mod okx;

pub(in crate::composition) use binance::{
    futures_private_connection as binance_futures_private_connection,
    options_private_connection as binance_options_private_connection,
    same_spot_provider_context as same_binance_spot_provider_context,
    spot_channel_config as binance_spot_channel_config,
    spot_private_connection as binance_spot_private_connection,
    spot_private_connection_from_provider as binance_spot_private_connection_from_provider,
    spot_provider_connection as binance_spot_provider_connection,
};
pub(in crate::composition) use ibkr::{
    compose_async_execution as compose_ibkr_async_execution, connection as ibkr_connection,
};
pub(in crate::composition) use okx::{
    private_connection as okx_private_connection,
    private_connection_from_provider as okx_private_connection_from_provider,
    provider_connection as okx_provider_connection,
    same_provider_context as same_okx_provider_context, trading_shape as okx_trading_shape,
};
