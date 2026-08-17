use super::super::super::connections::ExecutionConnectionOptions;
use super::common::{environment, quota, shared_quota};
use kairos_integration::participants::binance::{
    BinancePrincipalConfig, BinancePrincipalOrderQuotaAllocation, BinanceSpotChannelConfig,
    BinanceSpotConnection, BinanceSpotConnectionConfig, BinanceSpotPrincipalConnection,
};

pub(in crate::composition) fn provider_connection(
    options: &ExecutionConnectionOptions,
) -> Result<BinanceSpotConnection, String> {
    BinanceSpotConnection::connect(BinanceSpotConnectionConfig {
        environment: environment(options),
        rest_base_url: options.base_url.clone(),
        quota: quota(options),
        shared_quota: shared_quota(options),
    })
    .map_err(|error| error.to_string())
}

pub(in crate::composition) fn private_connection_from_provider(
    provider: &BinanceSpotConnection,
    options: &ExecutionConnectionOptions,
) -> Result<BinanceSpotPrincipalConnection, String> {
    provider
        .principal_connection(BinancePrincipalConfig {
            binding_id: format!("binance.principal.{}", options.principal_scope_id),
            principal_id: Some(options.principal_scope_id.clone()),
            api_key: options.api_key.clone(),
            secret: options.secret.clone(),
            principal_quota: options.shared_quota_ledger_path.as_ref().map(|_| {
                BinancePrincipalOrderQuotaAllocation {
                    orders_per_10_seconds: options.orders_per_10_seconds,
                    orders_per_day: options.orders_per_day,
                }
            }),
        })
        .map_err(|error| error.to_string())
}

pub(in crate::composition) fn channel_config(
    options: &ExecutionConnectionOptions,
) -> BinanceSpotChannelConfig {
    BinanceSpotChannelConfig {
        websocket_api_url: options.websocket_url.clone(),
        event_queue_capacity: options.order_event_queue_capacity,
    }
}

pub(in crate::composition) fn private_connection(
    options: &ExecutionConnectionOptions,
) -> Result<BinanceSpotPrincipalConnection, String> {
    let provider = provider_connection(options)?;
    private_connection_from_provider(&provider, options)
}

pub(in crate::composition) fn same_provider_context(
    left: &ExecutionConnectionOptions,
    right: &ExecutionConnectionOptions,
) -> bool {
    left.base_url == right.base_url
        && left.request_weight_per_minute == right.request_weight_per_minute
        && left.cancel_reserve_weight == right.cancel_reserve_weight
        && left.shared_quota_ledger_path == right.shared_quota_ledger_path
        && left.egress_scope_id == right.egress_scope_id
}
