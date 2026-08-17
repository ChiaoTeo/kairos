use super::super::super::connections::ExecutionConnectionOptions;
use kairos_integration::participants::binance::{BinanceQuotaAllocation, BinanceSharedQuotaConfig};

pub(super) fn environment(options: &ExecutionConnectionOptions) -> String {
    if options.base_url.to_ascii_lowercase().contains("testnet") {
        "testnet".into()
    } else {
        "live".into()
    }
}

pub(super) fn quota(options: &ExecutionConnectionOptions) -> BinanceQuotaAllocation {
    BinanceQuotaAllocation {
        request_weight_per_minute: options.request_weight_per_minute,
        cancel_reserve_weight: options.cancel_reserve_weight,
    }
}

pub(super) fn shared_quota(
    options: &ExecutionConnectionOptions,
) -> Option<BinanceSharedQuotaConfig> {
    options
        .shared_quota_ledger_path
        .clone()
        .map(|ledger_path| BinanceSharedQuotaConfig {
            ledger_path,
            egress_scope_id: options.egress_scope_id.clone(),
        })
}
