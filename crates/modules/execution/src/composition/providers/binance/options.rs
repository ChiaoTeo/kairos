use super::super::super::connections::ExecutionConnectionOptions;
use super::common::{environment, quota, shared_quota};
use kairos_integration::participants::binance::{
    BinanceOptionsConnection, BinanceOptionsConnectionConfig, BinancePrincipalConfig,
};

pub(in crate::composition) fn private_connection(
    options: &ExecutionConnectionOptions,
) -> Result<kairos_integration::participants::binance::BinanceOptionsPrincipalConnection, String> {
    let provider = BinanceOptionsConnection::connect(BinanceOptionsConnectionConfig {
        environment: environment(options),
        rest_base_url: options.base_url.clone(),
        quota: quota(options),
        shared_quota: shared_quota(options),
    })
    .map_err(|error| error.to_string())?;
    provider
        .principal_connection(BinancePrincipalConfig {
            binding_id: format!("binance.principal.{}", options.principal_scope_id),
            principal_id: Some(options.principal_scope_id.clone()),
            api_key: options.api_key.clone(),
            secret: options.secret.clone(),
            principal_quota: None,
        })
        .map_err(|error| error.to_string())
}
