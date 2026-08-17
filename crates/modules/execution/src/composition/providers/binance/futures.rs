use super::super::super::connections::ExecutionConnectionOptions;
use super::common::{environment, quota, shared_quota};
use kairos_integration::participants::binance::ConnectionDomain;
use kairos_integration::participants::binance::{
    BinanceCoinMConnection, BinanceFuturesConnectionConfig, BinancePrincipalConfig,
    BinanceUsdMConnection,
};

pub(in crate::composition) fn private_connection(
    options: &ExecutionConnectionOptions,
    domain: ConnectionDomain,
) -> Result<kairos_integration::participants::binance::BinanceFuturesPrincipalConnection, String> {
    let config = BinanceFuturesConnectionConfig {
        environment: environment(options),
        rest_base_url: options.base_url.clone(),
        quota: quota(options),
        shared_quota: shared_quota(options),
    };
    let principal = BinancePrincipalConfig {
        binding_id: format!("binance.principal.{}", options.principal_scope_id),
        principal_id: Some(options.principal_scope_id.clone()),
        api_key: options.api_key.clone(),
        secret: options.secret.clone(),
        principal_quota: None,
    };
    match domain {
        ConnectionDomain::UsdMFutures => BinanceUsdMConnection::connect(config)
            .map_err(|error| error.to_string())?
            .principal_connection(principal)
            .map_err(|error| error.to_string()),
        ConnectionDomain::CoinMFutures => BinanceCoinMConnection::connect(config)
            .map_err(|error| error.to_string())?
            .principal_connection(principal)
            .map_err(|error| error.to_string()),
        _ => Err(format!("unsupported Binance futures domain: {domain:?}")),
    }
}
