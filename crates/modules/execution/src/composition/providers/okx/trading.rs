//! OKX-native trading connection construction selected by Execution composition.

use super::super::super::connections::ExecutionConnectionOptions;
use kairos_integration::participants::okx::{
    InstrumentType as OkxInstrumentType, OkxConnection, OkxConnectionConfig, OkxPrincipalConfig,
    OkxPrincipalOrderQuotaAllocation, OkxPrincipalQuotaAllocation, OkxSharedQuotaConfig,
    TradingMode as OkxTradingMode,
};

pub(in crate::composition) fn trading_shape(
    product: &str,
    trading_mode: Option<&str>,
) -> Result<(OkxInstrumentType, OkxTradingMode), String> {
    let instrument_type = match product.trim().to_ascii_lowercase().as_str() {
        "spot" => OkxInstrumentType::Spot,
        "margin" => OkxInstrumentType::Margin,
        "swap" => OkxInstrumentType::Swap,
        "futures" => OkxInstrumentType::Futures,
        "option" | "options" => OkxInstrumentType::Option,
        other => return Err(format!("unsupported OKX execution product: {other}")),
    };
    let trading_mode = match trading_mode.map(|value| value.trim().to_ascii_lowercase()) {
        Some(value) if value == "cash" => OkxTradingMode::Cash,
        Some(value) if value == "cross" => OkxTradingMode::Cross,
        Some(value) if value == "isolated" => OkxTradingMode::Isolated,
        Some(value) => return Err(format!("unsupported OKX trading mode: {value}")),
        None if instrument_type == OkxInstrumentType::Spot => OkxTradingMode::Cash,
        None => {
            return Err(format!(
                "OKX {product} execution route requires explicit trading_mode (cross or isolated)"
            ))
        }
    };
    if instrument_type == OkxInstrumentType::Spot && trading_mode != OkxTradingMode::Cash {
        return Err("OKX spot execution requires cash trading_mode".into());
    }
    if instrument_type == OkxInstrumentType::Margin && trading_mode == OkxTradingMode::Cash {
        return Err("OKX margin execution requires cross or isolated trading_mode".into());
    }
    Ok((instrument_type, trading_mode))
}

pub(in crate::composition) fn provider_connection(
    options: &ExecutionConnectionOptions,
) -> Result<OkxConnection, String> {
    OkxConnection::connect(OkxConnectionConfig {
        environment: if options.base_url.to_ascii_lowercase().contains("demo")
            || options.base_url.to_ascii_lowercase().contains("test")
        {
            "demo".into()
        } else {
            "live".into()
        },
        rest_base_url: options.base_url.clone(),
        shared_quota: options.shared_quota_ledger_path.clone().map(|ledger_path| {
            OkxSharedQuotaConfig {
                ledger_path,
                egress_scope_id: options.egress_scope_id.clone(),
            }
        }),
    })
    .map_err(|error| error.to_string())
}

pub(in crate::composition) fn private_connection_from_provider(
    provider: &OkxConnection,
    options: &ExecutionConnectionOptions,
) -> Result<kairos_integration::participants::okx::OkxPrincipalConnection, String> {
    let quota_enabled = options.shared_quota_ledger_path.is_some();
    provider
        .principal_connection(OkxPrincipalConfig {
            binding_id: format!("okx.principal.{}", options.principal_scope_id),
            principal_id: Some(options.principal_scope_id.clone()),
            api_key: options.api_key.clone(),
            secret: options.secret.clone(),
            passphrase: options.passphrase.clone(),
            quota: quota_enabled.then_some(OkxPrincipalQuotaAllocation::default()),
            order_quota: quota_enabled.then_some(OkxPrincipalOrderQuotaAllocation::default()),
        })
        .map_err(|error| error.to_string())
}

pub(in crate::composition) fn private_connection(
    options: &ExecutionConnectionOptions,
) -> Result<kairos_integration::participants::okx::OkxPrincipalConnection, String> {
    let provider = provider_connection(options)?;
    private_connection_from_provider(&provider, options)
}

pub(in crate::composition) fn same_provider_context(
    left: &ExecutionConnectionOptions,
    right: &ExecutionConnectionOptions,
) -> bool {
    left.base_url == right.base_url
        && left.shared_quota_ledger_path == right.shared_quota_ledger_path
        && left.egress_scope_id == right.egress_scope_id
}
