use serde_json::Value;

use crate::{CommandOutcome, CommandResult, IntegrationError, ParticipantRejection};

rest_connection!(
    BinanceInstitutionalLoanRestConnection,
    "advanced.institutional.loan.rest"
);

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BinanceInstitutionalRiskUnit {
    pub group_id: String,
    pub status: Option<String>,
    pub credit_account: Option<String>,
    pub collateral_accounts: Vec<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BinanceInstitutionalLoanRequest {
    pub group_id: String,
    pub asset: String,
    pub amount: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BinanceInstitutionalRepayRequest {
    pub group_id: String,
    pub asset: String,
    pub amount: String,
    pub repay_from: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BinanceInstitutionalLoanReceipt {
    pub transaction_id: Option<String>,
    pub status: Option<String>,
}

impl BinanceInstitutionalLoanRestConnection {
    pub async fn active_risk_units(
        &mut self,
    ) -> Result<Vec<BinanceInstitutionalRiskUnit>, IntegrationError> {
        let value = self
            .service
            .signed_get("/sapi/v1/margin/loan-groups/activated", &[])
            .await?;
        risk_units(&value)
    }

    pub async fn risk_unit(
        &mut self,
        group_id: &str,
    ) -> Result<Option<BinanceInstitutionalRiskUnit>, IntegrationError> {
        require("group id", group_id)?;
        let value = self
            .service
            .signed_get(
                "/sapi/v1/margin/loan-group",
                &[("groupId", group_id.into())],
            )
            .await?;
        Ok(risk_units(&value)?.into_iter().next())
    }

    pub async fn borrow(
        &mut self,
        request: &BinanceInstitutionalLoanRequest,
    ) -> CommandResult<BinanceInstitutionalLoanReceipt> {
        validate_loan(request)?;
        let params = [
            ("groupId", request.group_id.clone()),
            ("loanCoin", request.asset.clone()),
            ("amount", request.amount.clone()),
        ];
        let outcome = self
            .service
            .signed_post_command("/sapi/v1/margin/loan-group/borrow", &params)
            .await?;
        normalize_receipt(outcome)
    }

    pub async fn repay(
        &mut self,
        request: &BinanceInstitutionalRepayRequest,
    ) -> CommandResult<BinanceInstitutionalLoanReceipt> {
        require("group id", &request.group_id)?;
        require("asset", &request.asset)?;
        require("amount", &request.amount)?;
        let mut params = vec![
            ("groupId", request.group_id.clone()),
            ("loanCoin", request.asset.clone()),
            ("amount", request.amount.clone()),
        ];
        if let Some(source) = &request.repay_from {
            params.push(("repayFrom", source.clone()));
        }
        let outcome = self
            .service
            .signed_post_command("/sapi/v1/margin/loan-group/repay", &params)
            .await?;
        normalize_receipt(outcome)
    }
}

fn risk_units(value: &Value) -> Result<Vec<BinanceInstitutionalRiskUnit>, IntegrationError> {
    let data = value.get("data").unwrap_or(value);
    let rows = data
        .as_array()
        .map(Vec::as_slice)
        .unwrap_or_else(|| std::slice::from_ref(data));
    rows.iter()
        .map(|row| {
            let group_id = field(row, &["groupId", "riskUnitId"]).ok_or_else(|| {
                IntegrationError::InvalidPayload(
                    "Binance Institutional Loan group id is missing".into(),
                )
            })?;
            let collateral_accounts = row
                .get("collateralAccounts")
                .or_else(|| row.get("memberAccounts"))
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(|value| value.as_str().map(str::to_owned))
                .collect();
            Ok(BinanceInstitutionalRiskUnit {
                group_id,
                status: field(row, &["status"]),
                credit_account: field(row, &["creditAccount", "creditAccountId"]),
                collateral_accounts,
            })
        })
        .collect()
}

fn normalize_receipt(
    outcome: CommandOutcome<Value>,
) -> CommandResult<BinanceInstitutionalLoanReceipt> {
    Ok(match outcome {
        CommandOutcome::Confirmed(value) => {
            let row = value.get("data").unwrap_or(&value);
            if value
                .get("code")
                .and_then(Value::as_i64)
                .is_some_and(|code| code < 0)
            {
                CommandOutcome::Rejected(ParticipantRejection {
                    code: value.get("code").map(Value::to_string),
                    message: value
                        .get("msg")
                        .and_then(Value::as_str)
                        .unwrap_or("Binance Institutional Loan rejected command")
                        .into(),
                    participant_request_id: None,
                })
            } else {
                CommandOutcome::Confirmed(BinanceInstitutionalLoanReceipt {
                    transaction_id: field(row, &["tranId", "transactionId"]),
                    status: field(row, &["status"]),
                })
            }
        },
        CommandOutcome::Rejected(error) => CommandOutcome::Rejected(error),
        CommandOutcome::Indeterminate(error) => CommandOutcome::Indeterminate(error),
    })
}

fn validate_loan(request: &BinanceInstitutionalLoanRequest) -> Result<(), IntegrationError> {
    require("group id", &request.group_id)?;
    require("asset", &request.asset)?;
    require("amount", &request.amount)
}

fn require(name: &str, value: &str) -> Result<(), IntegrationError> {
    if value.trim().is_empty() {
        Err(IntegrationError::InvalidRequest(format!(
            "Binance Institutional Loan {name} is required"
        )))
    } else {
        Ok(())
    }
}

fn field(value: &Value, fields: &[&str]) -> Option<String> {
    fields
        .iter()
        .find_map(|field| value.get(*field))
        .and_then(|value| {
            value
                .as_str()
                .map(str::to_owned)
                .or_else(|| value.as_u64().map(|value| value.to_string()))
        })
        .filter(|value| !value.is_empty())
}
