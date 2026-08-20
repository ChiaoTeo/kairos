use std::collections::BTreeMap;
use std::str::FromStr;

use kairos_primitives::account::{AccountId, SegmentKey};
use kairos_primitives::decimal::Quantity;
use kairos_primitives::time::UnixNanos;
use serde_json::Value;

use super::BinanceTransferAccount;
use crate::{
    AssetTransferCommand, AssetTransferQuery, AssetTransferRequest, AssetTransferState,
    AssetTransferStatus, AssetTransferStatusQuery, AssetTransferSubmission, CommandOutcome,
    ConnectionDescriptor, ConnectionKey, IntegrationError,
};

const TRANSFER_PATH: &str = "/sapi/v1/sub-account/universalTransfer";
const HISTORY_LIMIT: usize = 500;
const MATCH_EARLY_TOLERANCE_MILLIS: u64 = 60_000;
const MATCH_LATE_TOLERANCE_MILLIS: u64 = 300_000;
const MAX_HISTORY_RANGE_MILLIS: u64 = 7 * 24 * 60 * 60 * 1_000;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BinanceSubAccountIdentity {
    /// Binance email used by the master-account API. None denotes the master.
    pub email: Option<String>,
}

#[derive(Clone, Debug)]
pub struct BinanceSubAccountCapitalRestConfig {
    pub rest: crate::participants::binance::BinanceRestConfig,
    pub master_account_id: AccountId,
    pub accounts: BTreeMap<AccountId, BinanceSubAccountIdentity>,
    pub segment_accounts: BTreeMap<(AccountId, SegmentKey), BinanceTransferAccount>,
}

/// Master-authorized transfer rail between Binance master/subaccount ledgers.
pub struct BinanceSubAccountCapitalRestConnection {
    service: crate::services::participants::binance::rest::RestService,
    accounts: BTreeMap<AccountId, BinanceSubAccountIdentity>,
    segment_accounts: BTreeMap<(AccountId, SegmentKey), BinanceTransferAccount>,
}

impl BinanceSubAccountCapitalRestConnection {
    pub fn new(
        connection_key: ConnectionKey,
        config: BinanceSubAccountCapitalRestConfig,
    ) -> Result<Self, IntegrationError> {
        let master = config
            .accounts
            .get(&config.master_account_id)
            .ok_or_else(|| {
                IntegrationError::InvalidRequest(
                    "Binance subaccount rail does not include its master Account".into(),
                )
            })?;
        if master.email.is_some() {
            return Err(IntegrationError::InvalidRequest(
                "Binance master Account cannot have a subaccount email".into(),
            ));
        }
        if config.accounts.len() < 2 {
            return Err(IntegrationError::InvalidRequest(
                "Binance subaccount rail requires at least one subaccount".into(),
            ));
        }
        if config.accounts.iter().any(|(account_id, identity)| {
            account_id != &config.master_account_id
                && identity.email.as_deref().is_none_or(str::is_empty)
        }) {
            return Err(IntegrationError::InvalidRequest(
                "every Binance subaccount requires a non-empty email identity".into(),
            ));
        }
        let descriptor = config
            .rest
            .descriptor(connection_key, "capital.subaccount.rest")?;
        if descriptor.principal_id.as_deref() != Some(config.master_account_id.as_str()) {
            return Err(IntegrationError::Authorization(
                "Binance subaccount rail requires the master Account credential".into(),
            ));
        }
        let credential = config.rest.credential;
        Ok(Self {
            service: crate::services::participants::binance::rest::RestService::new(
                descriptor,
                config.rest.endpoint,
                credential,
            )?,
            accounts: config.accounts,
            segment_accounts: config.segment_accounts,
        })
    }

    pub fn descriptor(&self) -> &ConnectionDescriptor {
        self.service.descriptor()
    }

    fn route(&self, request: &AssetTransferRequest) -> Result<SubAccountRoute, IntegrationError> {
        request
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        if request.source.identity.account_id == request.destination.identity.account_id {
            return Err(IntegrationError::UnsupportedOperation);
        }
        if !request
            .source
            .identity
            .broker
            .eq_ignore_ascii_case("binance")
            || !request
                .destination
                .identity
                .broker
                .eq_ignore_ascii_case("binance")
        {
            return Err(IntegrationError::InvalidRequest(
                "Binance subaccount rail only accepts Binance locations".into(),
            ));
        }
        if request.source.environment != self.descriptor().environment {
            return Err(IntegrationError::InvalidRequest(
                "Binance subaccount transfer environment does not match its connection".into(),
            ));
        }
        let source_identity = self
            .accounts
            .get(&request.source.identity.account_id)
            .ok_or(IntegrationError::UnsupportedOperation)?;
        let destination_identity = self
            .accounts
            .get(&request.destination.identity.account_id)
            .ok_or(IntegrationError::UnsupportedOperation)?;
        let source_type = self
            .segment_accounts
            .get(&(
                request.source.identity.account_id.clone(),
                request.source.segment_key.clone(),
            ))
            .copied()
            .and_then(account_type)
            .ok_or(IntegrationError::UnsupportedOperation)?;
        let destination_type = self
            .segment_accounts
            .get(&(
                request.destination.identity.account_id.clone(),
                request.destination.segment_key.clone(),
            ))
            .copied()
            .and_then(account_type)
            .ok_or(IntegrationError::UnsupportedOperation)?;
        if !supported_route(
            source_identity.email.is_none(),
            source_type,
            destination_identity.email.is_none(),
            destination_type,
        ) {
            return Err(IntegrationError::UnsupportedOperation);
        }
        Ok(SubAccountRoute {
            from_email: source_identity.email.clone(),
            to_email: destination_identity.email.clone(),
            from_account_type: source_type,
            to_account_type: destination_type,
        })
    }
}

struct SubAccountRoute {
    from_email: Option<String>,
    to_email: Option<String>,
    from_account_type: &'static str,
    to_account_type: &'static str,
}

impl AssetTransferCommand for BinanceSubAccountCapitalRestConnection {
    async fn submit_transfer(
        &mut self,
        request: &AssetTransferRequest,
    ) -> crate::CommandResult<AssetTransferSubmission> {
        let route = self.route(request)?;
        let mut params = vec![
            ("fromAccountType", route.from_account_type.to_owned()),
            ("toAccountType", route.to_account_type.to_owned()),
            ("asset", request.asset.to_string()),
            ("amount", request.amount.to_string()),
            ("clientTranId", request.idempotency_key.to_string()),
        ];
        if let Some(email) = route.from_email {
            params.push(("fromEmail", email));
        }
        if let Some(email) = route.to_email {
            params.push(("toEmail", email));
        }
        self.service
            .signed_post_command(TRANSFER_PATH, &params)
            .await
            .and_then(|outcome| match outcome {
                CommandOutcome::Confirmed(value) => {
                    parse_submission(&value).map(CommandOutcome::Confirmed)
                },
                CommandOutcome::Rejected(value) => Ok(CommandOutcome::Rejected(value)),
                CommandOutcome::Indeterminate(value) => Ok(CommandOutcome::Indeterminate(value)),
            })
    }
}

impl AssetTransferStatusQuery for BinanceSubAccountCapitalRestConnection {
    async fn transfer_status(
        &mut self,
        query: &AssetTransferQuery,
    ) -> Result<Option<AssetTransferStatus>, IntegrationError> {
        // Materialize the route before borrowing the mutable REST service. The
        // route is pure request/configuration data and must not keep an
        // immutable borrow of `self` alive across the network await.
        let (from_email, to_email, from_account_type, to_account_type) = {
            let route = self.route(&query.request)?;
            (
                route.from_email,
                route.to_email,
                route.from_account_type,
                route.to_account_type,
            )
        };
        let requested_millis = query.request.requested_at_unix_nanos.get() / 1_000_000;
        let start = requested_millis.saturating_sub(MATCH_EARLY_TOLERANCE_MILLIS);
        let end = requested_millis
            .saturating_add(MATCH_LATE_TOLERANCE_MILLIS)
            .min(start.saturating_add(MAX_HISTORY_RANGE_MILLIS - 1));
        let params = vec![
            ("clientTranId", query.request.idempotency_key.to_string()),
            ("startTime", start.to_string()),
            ("endTime", end.to_string()),
            ("page", "1".into()),
            ("limit", HISTORY_LIMIT.to_string()),
        ];
        let value = self.service.signed_get(TRANSFER_PATH, &params).await?;
        let rows = value
            .get("result")
            .and_then(Value::as_array)
            .ok_or_else(|| {
                IntegrationError::InvalidPayload(
                    "Binance subaccount transfer history result is missing".into(),
                )
            })?;
        let route = SubAccountRoute {
            from_email,
            to_email,
            from_account_type,
            to_account_type,
        };
        let mut matches = rows
            .iter()
            .filter(|row| history_row_matches(row, query, &route))
            .map(|row| parse_status(row, query))
            .collect::<Result<Vec<_>, _>>()?;
        match matches.len() {
            0 => Ok(None),
            1 => Ok(matches.pop()),
            _ => Err(IntegrationError::InvalidPayload(
                "Binance subaccount transfer history is ambiguous for one clientTranId".into(),
            )),
        }
    }
}

fn account_type(value: BinanceTransferAccount) -> Option<&'static str> {
    match value {
        BinanceTransferAccount::Spot => Some("SPOT"),
        BinanceTransferAccount::UsdMFutures => Some("USDT_FUTURE"),
        BinanceTransferAccount::CoinMFutures => Some("COIN_FUTURE"),
        BinanceTransferAccount::CrossMargin => Some("MARGIN"),
        BinanceTransferAccount::Funding => None,
    }
}

fn supported_route(
    source_is_master: bool,
    source: &str,
    destination_is_master: bool,
    destination: &str,
) -> bool {
    if source == "SPOT" {
        return matches!(destination, "SPOT" | "USDT_FUTURE" | "COIN_FUTURE")
            || (source_is_master && !destination_is_master && destination == "MARGIN");
    }
    if destination == "SPOT" {
        return matches!(source, "USDT_FUTURE" | "COIN_FUTURE")
            || (!source_is_master && destination_is_master && source == "MARGIN");
    }
    !source_is_master && !destination_is_master && source == "MARGIN" && destination == "MARGIN"
}

fn parse_submission(value: &Value) -> Result<AssetTransferSubmission, IntegrationError> {
    Ok(AssetTransferSubmission {
        participant_transfer_id: scalar_string(value.get("tranId")),
        acknowledged_at_unix_nanos: None,
    })
}

fn history_row_matches(row: &Value, query: &AssetTransferQuery, route: &SubAccountRoute) -> bool {
    scalar_string(row.get("clientTranId")).as_deref()
        == Some(query.request.idempotency_key.as_str())
        && participant_email_matches(
            row.get("fromEmail").and_then(Value::as_str),
            route.from_email.as_deref(),
        )
        && participant_email_matches(
            row.get("toEmail").and_then(Value::as_str),
            route.to_email.as_deref(),
        )
        && row.get("fromAccountType").and_then(Value::as_str) == Some(route.from_account_type)
        && row.get("toAccountType").and_then(Value::as_str) == Some(route.to_account_type)
        && row.get("asset").and_then(Value::as_str) == Some(query.request.asset.as_str())
        && row
            .get("amount")
            .and_then(Value::as_str)
            .and_then(|value| Quantity::from_str(value).ok())
            .is_some_and(|amount| amount == query.request.amount)
}

fn participant_email_matches(actual: Option<&str>, expected: Option<&str>) -> bool {
    match expected {
        Some(expected) => actual == Some(expected),
        // Binance may echo the authenticated master email even though the
        // request identifies the master by omitting the email field.
        None => true,
    }
}

fn parse_status(
    row: &Value,
    query: &AssetTransferQuery,
) -> Result<AssetTransferStatus, IntegrationError> {
    let participant_state = row
        .get("status")
        .and_then(Value::as_str)
        .ok_or_else(|| {
            IntegrationError::InvalidPayload("Binance subaccount transfer status is missing".into())
        })?
        .to_owned();
    let state = match participant_state.as_str() {
        "PENDING" | "PROCESS" | "PROCESSING" => AssetTransferState::Pending,
        "SUCCESS" | "SUCCEEDED" => AssetTransferState::Succeeded,
        "FAILURE" | "FAILED" => AssetTransferState::Failed,
        "CANCELLED" | "CANCELED" => AssetTransferState::Cancelled,
        _ => AssetTransferState::Unknown,
    };
    let settled_amount = if state == AssetTransferState::Succeeded {
        row.get("amount")
            .and_then(Value::as_str)
            .map(Quantity::from_str)
            .transpose()
            .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?
    } else {
        None
    };
    Ok(AssetTransferStatus {
        idempotency_key: query.request.idempotency_key.clone(),
        participant_transfer_id: scalar_string(row.get("tranId")),
        source: query.request.source.clone(),
        destination: query.request.destination.clone(),
        asset: query.request.asset.clone(),
        requested_amount: query.request.amount,
        settled_amount,
        state,
        participant_state: Some(participant_state),
        updated_at_unix_nanos: row
            .get("createTimeStamp")
            .and_then(Value::as_u64)
            .and_then(|millis| millis.checked_mul(1_000_000))
            .map(UnixNanos::new),
        failure_reason: row
            .get("reason")
            .or_else(|| row.get("msg"))
            .and_then(Value::as_str)
            .map(str::to_owned),
    })
}

fn scalar_string(value: Option<&Value>) -> Option<String> {
    match value? {
        Value::String(value) if !value.is_empty() => Some(value.clone()),
        Value::Number(value) => Some(value.to_string()),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn supported_routes_match_the_documented_master_subaccount_matrix() {
        assert!(supported_route(true, "SPOT", false, "USDT_FUTURE"));
        assert!(supported_route(false, "USDT_FUTURE", true, "SPOT"));
        assert!(supported_route(false, "MARGIN", false, "MARGIN"));
        assert!(!supported_route(true, "USDT_FUTURE", false, "USDT_FUTURE"));
        assert!(!supported_route(false, "MARGIN", false, "USDT_FUTURE"));
    }

    #[test]
    fn funding_wallet_is_not_a_subaccount_universal_transfer_type() {
        assert_eq!(account_type(BinanceTransferAccount::Funding), None);
    }

    #[test]
    fn submission_preserves_large_transaction_identifiers() {
        assert_eq!(
            parse_submission(&serde_json::json!({
                "tranId": 11945860693_u64,
                "clientTranId": "capital:plan-1"
            }))
            .unwrap()
            .participant_transfer_id
            .as_deref(),
            Some("11945860693")
        );
    }

    #[test]
    fn master_history_email_may_be_absent_or_echoed() {
        assert!(participant_email_matches(None, None));
        assert!(participant_email_matches(Some("master@example.com"), None));
        assert!(participant_email_matches(
            Some("sub@example.com"),
            Some("sub@example.com")
        ));
        assert!(!participant_email_matches(
            Some("other@example.com"),
            Some("sub@example.com")
        ));
    }
}
