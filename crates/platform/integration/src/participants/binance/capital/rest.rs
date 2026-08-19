use std::collections::BTreeMap;
use std::str::FromStr;

use kairos_primitives::{Quantity, SegmentKey, UnixNanos};
use serde_json::Value;

use crate::{
    AssetTransferCommand, AssetTransferQuery, AssetTransferRequest, AssetTransferState,
    AssetTransferStatus, AssetTransferStatusQuery, AssetTransferSubmission, CommandOutcome,
    ConnectionDescriptor, ConnectionKey, IntegrationError,
};

const TRANSFER_PATH: &str = "/sapi/v1/asset/transfer";
const HISTORY_PAGE_SIZE: usize = 100;
const MAX_HISTORY_PAGES: usize = 100;
const MATCH_EARLY_TOLERANCE_MILLIS: u64 = 60_000;
const MATCH_LATE_TOLERANCE_MILLIS: u64 = 300_000;

/// Binance wallet identities accepted by User Universal Transfer.
///
/// Capital config binds its opaque Account segment keys to this provider-owned
/// enum. Strategy code never supplies Binance transfer type strings.
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum BinanceTransferAccount {
    Spot,
    Funding,
    UsdMFutures,
    CoinMFutures,
    CrossMargin,
}

#[derive(Clone, Debug)]
pub struct BinanceCapitalRestConfig {
    pub rest: crate::participants::binance::BinanceRestConfig,
    pub segment_accounts: BTreeMap<SegmentKey, BinanceTransferAccount>,
}

/// Concrete Binance capital connection for intra-account wallet transfers.
///
/// Transfers between master/subaccounts use a different Binance endpoint and
/// will be exposed by a separate concrete rail instead of overloading this one.
pub struct BinanceCapitalRestConnection {
    service: crate::services::participants::binance::rest::RestService,
    segment_accounts: BTreeMap<SegmentKey, BinanceTransferAccount>,
}

impl BinanceCapitalRestConnection {
    pub fn new(
        connection_key: ConnectionKey,
        config: BinanceCapitalRestConfig,
    ) -> Result<Self, IntegrationError> {
        if config.segment_accounts.is_empty() {
            return Err(IntegrationError::InvalidRequest(
                "Binance Capital requires at least one segment binding".into(),
            ));
        }
        let descriptor = config.rest.descriptor(connection_key, "capital.rest")?;
        let credential = config.rest.credential;
        Ok(Self {
            service: crate::services::participants::binance::rest::RestService::new(
                descriptor,
                config.rest.endpoint,
                credential,
            )?,
            segment_accounts: config.segment_accounts,
        })
    }

    pub fn descriptor(&self) -> &ConnectionDescriptor {
        self.service.descriptor()
    }

    pub fn endpoint(&self) -> &str {
        self.service.endpoint()
    }

    pub fn rate_limit_headers(&self) -> BTreeMap<String, String> {
        self.service.rate_limit_headers()
    }

    pub fn clock_health(&self) -> crate::ProviderClockHealth {
        self.service.clock_health()
    }

    fn transfer_type(
        &self,
        request: &AssetTransferRequest,
    ) -> Result<&'static str, IntegrationError> {
        validate_request_scope(self.descriptor(), request)?;
        let source = self
            .segment_accounts
            .get(&request.source.segment_key)
            .copied()
            .ok_or_else(|| {
                IntegrationError::InvalidRequest(format!(
                    "Binance Capital has no binding for source segment '{}'",
                    request.source.segment_key
                ))
            })?;
        let destination = self
            .segment_accounts
            .get(&request.destination.segment_key)
            .copied()
            .ok_or_else(|| {
                IntegrationError::InvalidRequest(format!(
                    "Binance Capital has no binding for destination segment '{}'",
                    request.destination.segment_key
                ))
            })?;
        universal_transfer_type(source, destination).ok_or(IntegrationError::UnsupportedOperation)
    }
}

impl AssetTransferCommand for BinanceCapitalRestConnection {
    async fn submit_transfer(
        &mut self,
        request: &AssetTransferRequest,
    ) -> crate::CommandResult<AssetTransferSubmission> {
        request
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        let transfer_type = self.transfer_type(request)?;
        let params = [
            ("type", transfer_type.to_owned()),
            ("asset", request.asset.to_string()),
            ("amount", request.amount.to_string()),
        ];
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

impl AssetTransferStatusQuery for BinanceCapitalRestConnection {
    async fn transfer_status(
        &mut self,
        query: &AssetTransferQuery,
    ) -> Result<Option<AssetTransferStatus>, IntegrationError> {
        query
            .request
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        let transfer_type = self.transfer_type(&query.request)?;
        let requested_millis = query.request.requested_at_unix_nanos.get() / 1_000_000;
        let start_millis = requested_millis.saturating_sub(MATCH_EARLY_TOLERANCE_MILLIS);
        let mut matches = Vec::new();

        for page in 1..=MAX_HISTORY_PAGES {
            let params = [
                ("type", transfer_type.to_owned()),
                ("startTime", start_millis.to_string()),
                ("current", page.to_string()),
                ("size", HISTORY_PAGE_SIZE.to_string()),
            ];
            let value = self.service.signed_get(TRANSFER_PATH, &params).await?;
            let page_rows = history_rows(&value)?;
            for row in page_rows
                .iter()
                .filter(|row| history_row_matches(row, query, transfer_type, requested_millis))
            {
                matches.push(parse_status(row, query)?);
            }

            let total = value.get("total").and_then(Value::as_u64);
            let exhausted = page_rows.len() < HISTORY_PAGE_SIZE
                || total.is_some_and(|total| page * HISTORY_PAGE_SIZE >= total as usize);
            if exhausted {
                break;
            }
            if page == MAX_HISTORY_PAGES {
                return Err(IntegrationError::InvalidPayload(
                    "Binance transfer history exceeded the reconciliation page limit".into(),
                ));
            }
        }

        match matches.len() {
            0 => Ok(None),
            1 => Ok(matches.pop()),
            _ => Err(IntegrationError::InvalidPayload(
                "Binance transfer history is ambiguous for this operation; manual reconciliation is required"
                    .into(),
            )),
        }
    }
}

fn validate_request_scope(
    descriptor: &ConnectionDescriptor,
    request: &AssetTransferRequest,
) -> Result<(), IntegrationError> {
    if request.source.identity.account_id != request.destination.identity.account_id {
        return Err(IntegrationError::UnsupportedOperation);
    }
    if !request
        .source
        .identity
        .broker
        .eq_ignore_ascii_case("binance")
    {
        return Err(IntegrationError::InvalidRequest(
            "Binance Capital only accepts Binance account locations".into(),
        ));
    }
    if request.source.environment != descriptor.environment {
        return Err(IntegrationError::InvalidRequest(format!(
            "transfer environment '{}' does not match connection environment '{}'",
            request.source.environment, descriptor.environment
        )));
    }
    if descriptor.principal_id.as_deref() != Some(request.source.identity.account_id.as_str()) {
        return Err(IntegrationError::Authorization(format!(
            "Binance credential principal does not own account '{}'",
            request.source.identity.account_id
        )));
    }
    Ok(())
}

fn universal_transfer_type(
    source: BinanceTransferAccount,
    destination: BinanceTransferAccount,
) -> Option<&'static str> {
    use BinanceTransferAccount::{
        CoinMFutures as CoinM, CrossMargin as Margin, Funding, Spot, UsdMFutures as UsdM,
    };
    match (source, destination) {
        (Spot, Funding) => Some("MAIN_FUNDING"),
        (Funding, Spot) => Some("FUNDING_MAIN"),
        (Spot, UsdM) => Some("MAIN_UMFUTURE"),
        (UsdM, Spot) => Some("UMFUTURE_MAIN"),
        (Spot, CoinM) => Some("MAIN_CMFUTURE"),
        (CoinM, Spot) => Some("CMFUTURE_MAIN"),
        (Spot, Margin) => Some("MAIN_MARGIN"),
        (Margin, Spot) => Some("MARGIN_MAIN"),
        (Funding, UsdM) => Some("FUNDING_UMFUTURE"),
        (UsdM, Funding) => Some("UMFUTURE_FUNDING"),
        (Funding, CoinM) => Some("FUNDING_CMFUTURE"),
        (CoinM, Funding) => Some("CMFUTURE_FUNDING"),
        (Funding, Margin) => Some("FUNDING_MARGIN"),
        (Margin, Funding) => Some("MARGIN_FUNDING"),
        (UsdM, Margin) => Some("UMFUTURE_MARGIN"),
        (Margin, UsdM) => Some("MARGIN_UMFUTURE"),
        (CoinM, Margin) => Some("CMFUTURE_MARGIN"),
        (Margin, CoinM) => Some("MARGIN_CMFUTURE"),
        _ => None,
    }
}

fn parse_submission(value: &Value) -> Result<AssetTransferSubmission, IntegrationError> {
    Ok(AssetTransferSubmission {
        participant_transfer_id: Some(required_scalar_string(value, "tranId")?),
        acknowledged_at_unix_nanos: None,
    })
}

fn history_rows(value: &Value) -> Result<&[Value], IntegrationError> {
    value
        .get("rows")
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .ok_or_else(|| {
            IntegrationError::InvalidPayload("Binance transfer history rows are missing".into())
        })
}

fn history_row_matches(
    row: &Value,
    query: &AssetTransferQuery,
    transfer_type: &str,
    requested_millis: u64,
) -> bool {
    let transfer_id_matches = query
        .participant_transfer_id
        .as_ref()
        .is_none_or(|expected| {
            scalar_string(row.get("tranId")).as_deref() == Some(expected.as_str())
        });
    let amount_matches = row
        .get("amount")
        .and_then(Value::as_str)
        .and_then(|value| Quantity::from_str(value).ok())
        .is_some_and(|amount| amount == query.request.amount);
    let timestamp_matches = row
        .get("timestamp")
        .and_then(Value::as_u64)
        .is_some_and(|timestamp| {
            timestamp >= requested_millis.saturating_sub(MATCH_EARLY_TOLERANCE_MILLIS)
                && timestamp <= requested_millis.saturating_add(MATCH_LATE_TOLERANCE_MILLIS)
        });
    transfer_id_matches
        && row.get("type").and_then(Value::as_str) == Some(transfer_type)
        && row.get("asset").and_then(Value::as_str) == Some(query.request.asset.as_str())
        && amount_matches
        && timestamp_matches
}

fn parse_status(
    row: &Value,
    query: &AssetTransferQuery,
) -> Result<AssetTransferStatus, IntegrationError> {
    let participant_state = row
        .get("status")
        .and_then(Value::as_str)
        .ok_or_else(|| {
            IntegrationError::InvalidPayload("Binance transfer status is missing".into())
        })?
        .to_owned();
    let state = match participant_state.as_str() {
        "PENDING" | "PROCESSING" => AssetTransferState::Pending,
        "CONFIRMED" | "SUCCESS" | "SUCCEEDED" => AssetTransferState::Succeeded,
        "FAILED" | "FAILURE" => AssetTransferState::Failed,
        "CANCELLED" | "CANCELED" => AssetTransferState::Cancelled,
        _ => AssetTransferState::Unknown,
    };
    let amount = row
        .get("amount")
        .and_then(Value::as_str)
        .ok_or_else(|| {
            IntegrationError::InvalidPayload("Binance transfer amount is missing".into())
        })?
        .parse::<Quantity>()
        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?;
    let updated_at_unix_nanos = row
        .get("timestamp")
        .and_then(Value::as_u64)
        .and_then(|millis| millis.checked_mul(1_000_000))
        .map(UnixNanos::new);
    let failure_reason = row
        .get("reason")
        .or_else(|| row.get("msg"))
        .and_then(Value::as_str)
        .map(str::to_owned);

    Ok(AssetTransferStatus {
        idempotency_key: query.request.idempotency_key.clone(),
        participant_transfer_id: Some(required_scalar_string(row, "tranId")?),
        source: query.request.source.clone(),
        destination: query.request.destination.clone(),
        asset: query.request.asset.clone(),
        requested_amount: query.request.amount,
        settled_amount: (state == AssetTransferState::Succeeded).then_some(amount),
        state,
        participant_state: Some(participant_state),
        updated_at_unix_nanos,
        failure_reason,
    })
}

fn required_scalar_string(value: &Value, field: &str) -> Result<String, IntegrationError> {
    scalar_string(value.get(field)).ok_or_else(|| {
        IntegrationError::InvalidPayload(format!("Binance transfer {field} is missing"))
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
    use kairos_primitives::{AccountId, Currency, IdempotencyKey};

    use super::*;
    use crate::{ExternalAccountIdentity, ExternalAccountSegment};

    fn segment(key: &str) -> ExternalAccountSegment {
        ExternalAccountSegment {
            identity: ExternalAccountIdentity {
                broker: "binance".into(),
                account_id: AccountId::new("strategy-a").unwrap(),
            },
            segment_key: SegmentKey::new(key).unwrap(),
            environment: "live".into(),
            account_model: None,
        }
    }

    fn query(participant_transfer_id: Option<&str>) -> AssetTransferQuery {
        AssetTransferQuery {
            request: AssetTransferRequest {
                idempotency_key: IdempotencyKey::new("capital:plan-1:transfer:0").unwrap(),
                source: segment("funding"),
                destination: segment("usd-m"),
                asset: Currency::new("USDT").unwrap(),
                amount: Quantity::from_str("123.4500").unwrap(),
                requested_at_unix_nanos: UnixNanos::new(1_700_000_000_000_000_000),
                reason: None,
            },
            participant_transfer_id: participant_transfer_id.map(str::to_owned),
        }
    }

    #[test]
    fn maps_funding_and_usd_m_without_accepting_magic_strings() {
        assert_eq!(
            universal_transfer_type(
                BinanceTransferAccount::Funding,
                BinanceTransferAccount::UsdMFutures
            ),
            Some("FUNDING_UMFUTURE")
        );
        assert_eq!(
            universal_transfer_type(
                BinanceTransferAccount::UsdMFutures,
                BinanceTransferAccount::Funding
            ),
            Some("UMFUTURE_FUNDING")
        );
        assert_eq!(
            universal_transfer_type(
                BinanceTransferAccount::Funding,
                BinanceTransferAccount::Funding
            ),
            None
        );
    }

    #[test]
    fn parses_submission_ids_without_losing_integer_precision() {
        assert_eq!(
            parse_submission(&serde_json::json!({"tranId": 11415955596_u64}))
                .unwrap()
                .participant_transfer_id
                .as_deref(),
            Some("11415955596")
        );
    }

    #[test]
    fn preserves_unknown_participant_state() {
        let query = query(Some("11415955596"));
        let row = serde_json::json!({
            "asset": "USDT",
            "amount": "123.4500",
            "type": "FUNDING_UMFUTURE",
            "status": "REVIEWING",
            "tranId": 11415955596_u64,
            "timestamp": 1_700_000_000_010_u64
        });
        let status = parse_status(&row, &query).unwrap();
        assert_eq!(status.state, AssetTransferState::Unknown);
        assert_eq!(status.participant_state.as_deref(), Some("REVIEWING"));
        assert_eq!(status.settled_amount, None);
    }

    #[test]
    fn matches_by_full_route_when_submission_id_is_unavailable() {
        let query = query(None);
        let row = serde_json::json!({
            "asset": "USDT",
            "amount": "123.45",
            "type": "FUNDING_UMFUTURE",
            "status": "CONFIRMED",
            "tranId": 11415955596_u64,
            "timestamp": 1_700_000_000_010_u64
        });
        assert!(history_row_matches(
            &row,
            &query,
            "FUNDING_UMFUTURE",
            1_700_000_000_000
        ));
    }

    #[test]
    fn rejects_mismatched_transfer_id_even_when_route_matches() {
        let query = query(Some("different"));
        let row = serde_json::json!({
            "asset": "USDT",
            "amount": "123.45",
            "type": "FUNDING_UMFUTURE",
            "status": "CONFIRMED",
            "tranId": 11415955596_u64,
            "timestamp": 1_700_000_000_010_u64
        });
        assert!(!history_row_matches(
            &row,
            &query,
            "FUNDING_UMFUTURE",
            1_700_000_000_000
        ));
    }
}
