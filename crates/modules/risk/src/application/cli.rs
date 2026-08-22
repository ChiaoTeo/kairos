use std::path::{Path, PathBuf};

use kairos_workspace::Workspace;
use serde_json::{Value, json};

use crate::application::contract::{authorize_from, policy_from};
use crate::application::{PublishPolicy, RiskApplication};
use crate::services::actor::RiskActor;

/// Standalone Risk CLI facade.
///
/// This facade is reserved for local policy/schema/dry-run risk previews. It
/// must not create reservations, authorize runtime orders, connect to the Risk
/// server, or read runtime projections.
pub struct CliRiskApplication {
    workspace_root: PathBuf,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RiskCliRequestKind {
    Authorization,
    Policy,
}

impl CliRiskApplication {
    pub fn open(workspace: &Workspace) -> Self {
        Self {
            workspace_root: workspace.root().to_path_buf(),
        }
    }

    pub fn workspace_root(&self) -> &Path {
        &self.workspace_root
    }

    pub fn schema(&self, kind: Option<RiskCliRequestKind>) -> Value {
        let schemas = match kind {
            Some(RiskCliRequestKind::Authorization) => {
                vec![authorization_request_schema()]
            },
            Some(RiskCliRequestKind::Policy) => vec![policy_request_schema()],
            None => vec![authorization_request_schema(), policy_request_schema()],
        };
        json!({
            "owner": "risk",
            "mode": "standalone",
            "workspace_root": self.workspace_root.display().to_string(),
            "schemas": schemas,
        })
    }

    pub fn doctor(
        &self,
        kind: RiskCliRequestKind,
        file: &Path,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        let data = std::fs::read_to_string(file)?;
        let result = match kind {
            RiskCliRequestKind::Authorization => {
                match serde_json::from_str::<kairos_risk_contract::AuthorizeRequest>(&data) {
                    Ok(request) => json!({
                        "owner": "risk",
                        "mode": "standalone",
                        "kind": "authorization",
                        "valid": true,
                        "file": file.display().to_string(),
                        "request_id": request.request_id,
                        "reservation_id": request.reservation_id,
                        "account_id": request.account_id,
                        "strategy_id": request.strategy_id,
                        "instrument_id": request.instrument_id,
                        "exchange_id": request.exchange_id,
                    }),
                    Err(error) => json!({
                        "owner": "risk",
                        "mode": "standalone",
                        "kind": "authorization",
                        "valid": false,
                        "file": file.display().to_string(),
                        "error": error.to_string(),
                    }),
                }
            },
            RiskCliRequestKind::Policy => {
                match serde_json::from_str::<kairos_risk_contract::PublishPolicyRequest>(&data) {
                    Ok(request) => json!({
                        "owner": "risk",
                        "mode": "standalone",
                        "kind": "policy",
                        "valid": true,
                        "file": file.display().to_string(),
                        "policy_id": request.policy.policy_id,
                        "version": request.policy.version,
                        "metric": request.policy.metric.as_str(),
                        "enforcement": enforcement_mode(request.policy.enforcement),
                    }),
                    Err(error) => json!({
                        "owner": "risk",
                        "mode": "standalone",
                        "kind": "policy",
                        "valid": false,
                        "file": file.display().to_string(),
                        "error": error.to_string(),
                    }),
                }
            },
        };
        Ok(result)
    }

    pub fn preview(
        &self,
        policy_files: &[PathBuf],
        request_file: &Path,
    ) -> Result<Value, Box<dyn std::error::Error>> {
        if policy_files.is_empty() {
            return Err("at least one --policy-file is required for standalone preview".into());
        }
        let actor = RiskActor::new("risk-cli", Vec::new(), None).map_err(invalid_data)?;
        let mut application = RiskApplication::new(actor);
        for policy_file in policy_files {
            let request: kairos_risk_contract::PublishPolicyRequest = read_json_file(policy_file)?;
            application.publish_policy(PublishPolicy {
                policy: policy_from(request.policy).map_err(invalid_data)?,
            })?;
        }
        let request: kairos_risk_contract::AuthorizeRequest = read_json_file(request_file)?;
        let decision =
            application.pre_trade_check(authorize_from(request).map_err(invalid_data)?)?;
        Ok(json!({
            "owner": "risk",
            "mode": "standalone",
            "effect": "dry_run",
            "source": {
                "policy_files": policy_files
                    .iter()
                    .map(|path| path.display().to_string())
                    .collect::<Vec<_>>(),
                "request_file": request_file.display().to_string(),
            },
            "decision": decision,
        }))
    }
}

fn read_json_file<T: serde::de::DeserializeOwned>(
    path: &Path,
) -> Result<T, Box<dyn std::error::Error>> {
    let data = std::fs::read_to_string(path)?;
    Ok(serde_json::from_str(&data)?)
}

fn invalid_data(error: String) -> std::io::Error {
    std::io::Error::new(std::io::ErrorKind::InvalidData, error)
}

fn authorization_request_schema() -> Value {
    json!({
        "kind": "authorization",
        "used_by": [
            "connected pre-trade-check --file",
            "connected authorize-reserve --file"
        ],
        "type": "AuthorizeRequest",
        "required_fields": [
            "request_id",
            "idempotency_key",
            "reservation_id",
            "account_id",
            "strategy_id",
            "instrument_id",
            "exchange_id",
            "proposal",
            "at_unix_nanos",
            "reservation_ttl_nanos",
            "dependency_generation",
            "dependency_event_sequence"
        ],
        "optional_fields": ["context"],
        "proposal_required_fields": [
            "notional",
            "initial_margin_rate_bps",
            "account_segment",
            "collateral_asset",
            "margin_rule_id"
        ],
        "proposal_optional_fields": ["reduce_only"],
        "decimal_fields": ["proposal.notional"],
        "example": {
            "request_id": "request-1",
            "idempotency_key": "order-1:risk",
            "reservation_id": "reservation-1",
            "account_id": "account-1",
            "strategy_id": "strategy-1",
            "instrument_id": "BTC-USDT",
            "exchange_id": "binance",
            "proposal": {
                "notional": "100.00",
                "initial_margin_rate_bps": 1000,
                "account_segment": "spot",
                "collateral_asset": "USDT",
                "reduce_only": false,
                "margin_rule_id": "default"
            },
            "at_unix_nanos": 1,
            "reservation_ttl_nanos": 60000000000u64,
            "dependency_generation": 1,
            "dependency_event_sequence": 1
        }
    })
}

fn policy_request_schema() -> Value {
    json!({
        "kind": "policy",
        "used_by": ["connected publish-policy --file"],
        "type": "PublishPolicyRequest",
        "required_fields": ["policy"],
        "policy_required_fields": [
            "policy_id",
            "version",
            "scope",
            "metric",
            "limit",
            "enforcement",
            "valid_from_unix_nanos",
            "valid_until_unix_nanos"
        ],
        "policy_optional_fields": ["window_nanos"],
        "scope_fields": [
            "account_id",
            "strategy_id",
            "instrument_id",
            "exchange_id"
        ],
        "metrics": [
            "notional",
            "margin",
            "gross_exposure",
            "net_exposure",
            "turnover",
            "order_rate",
            "daily_loss",
            "drawdown",
            "leverage",
            "price_deviation",
            "stress_loss"
        ],
        "enforcement": ["reject", "warn", "observe"],
        "decimal_fields": ["policy.limit"],
        "example": {
            "policy": {
                "policy_id": "policy-1",
                "version": 1,
                "scope": {
                    "account_id": "account-1",
                    "strategy_id": null,
                    "instrument_id": null,
                    "exchange_id": "binance"
                },
                "metric": "notional",
                "limit": "1000.00",
                "enforcement": "reject",
                "valid_from_unix_nanos": 1,
                "valid_until_unix_nanos": null
            }
        }
    })
}

fn enforcement_mode(value: kairos_risk_contract::EnforcementMode) -> &'static str {
    match value {
        kairos_risk_contract::EnforcementMode::Reject => "reject",
        kairos_risk_contract::EnforcementMode::Warn => "warn",
        kairos_risk_contract::EnforcementMode::Observe => "observe",
    }
}
