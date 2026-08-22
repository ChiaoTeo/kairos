use std::str::FromStr;

use kairos_primitives::decimal::{Quantity, Rate, SignedQuantity};
use kairos_primitives::time::UnixNanos;
use serde_json::Value;

use crate::{
    CommandOutcome, ConnectionDescriptor, ConnectionKey, EarnActionKind, EarnActionQuery,
    EarnActionState, EarnActionStatus, EarnActionStatusQuery, EarnCommand, EarnLiquidity, EarnPage,
    EarnPosition, EarnPositionState, EarnPositionsRequest, EarnProduct, EarnProductFamily,
    EarnProductQuery, EarnProductState, EarnProductsRequest, EarnRateComponent,
    EarnRateComponentKind, EarnRateKind, EarnRateObservation, EarnRatesRequest, EarnRedeemRequest,
    EarnRedemptionAmount, EarnRedemptionChannel, EarnRedemptionOption, EarnReward,
    EarnRewardsRequest, EarnSubmission, EarnSubscribeRequest, EarnSubscriptionEligibility,
    EarnSubscriptionPreview, EarnSubscriptionPreviewRequest, IntegrationError,
    ParticipantRejection,
};

const FLEXIBLE_LIST: &str = "/sapi/v1/simple-earn/flexible/list";
const FLEXIBLE_POSITION: &str = "/sapi/v1/simple-earn/flexible/position";
const LOCKED_POSITION: &str = "/sapi/v1/simple-earn/locked/position";
const PERSONAL_QUOTA: &str = "/sapi/v1/simple-earn/flexible/personalLeftQuota";
const SUBSCRIBE: &str = "/sapi/v1/simple-earn/flexible/subscribe";
const REDEEM: &str = "/sapi/v1/simple-earn/flexible/redeem";
const SUBSCRIPTION_HISTORY: &str = "/sapi/v1/simple-earn/flexible/history/subscriptionRecord";
const REDEMPTION_HISTORY: &str = "/sapi/v1/simple-earn/flexible/history/redemptionRecord";
const REWARD_HISTORY: &str = "/sapi/v1/simple-earn/flexible/history/rewardsRecord";
const RATE_HISTORY: &str = "/sapi/v1/simple-earn/flexible/history/rateHistory";

pub struct BinanceSimpleEarnRestConnection {
    service: crate::services::participants::binance::rest::RestService,
}

impl BinanceSimpleEarnRestConnection {
    pub fn new(
        connection_key: ConnectionKey,
        config: crate::participants::binance::BinanceRestConfig,
    ) -> Result<Self, IntegrationError> {
        let descriptor = config.descriptor(connection_key, "simple-earn.rest")?;
        Ok(Self {
            service: crate::services::participants::binance::rest::RestService::new(
                descriptor,
                config.endpoint,
                config.credential,
            )?,
        })
    }

    pub fn descriptor(&self) -> &ConnectionDescriptor {
        self.service.descriptor()
    }
}

impl EarnProductQuery for BinanceSimpleEarnRestConnection {
    async fn products(
        &mut self,
        request: &EarnProductsRequest,
    ) -> Result<EarnPage<EarnProduct>, IntegrationError> {
        request
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        require_flexible(request.family.as_ref())?;
        let params = page_params(
            request.asset.as_ref().map(ToString::to_string),
            request.product_id.clone(),
            request.cursor.as_deref(),
            request.limit,
        );
        let value = self.service.signed_get(FLEXIBLE_LIST, &params).await?;
        parse_page(&value, parse_product)
    }

    async fn positions(
        &mut self,
        request: &EarnPositionsRequest,
    ) -> Result<EarnPage<EarnPosition>, IntegrationError> {
        request
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        let family = request
            .family
            .as_ref()
            .unwrap_or(&EarnProductFamily::Flexible);
        let (endpoint, product_field, parser) = match family {
            EarnProductFamily::Flexible => (
                FLEXIBLE_POSITION,
                "productId",
                parse_position as fn(&Value) -> Result<EarnPosition, IntegrationError>,
            ),
            EarnProductFamily::Locked => (
                LOCKED_POSITION,
                "projectId",
                parse_locked_position as fn(&Value) -> Result<EarnPosition, IntegrationError>,
            ),
            _ => return Err(IntegrationError::UnsupportedOperation),
        };
        let params = position_page_params(
            request.asset.as_ref().map(ToString::to_string),
            product_field,
            request.product_id.clone(),
            request.cursor.as_deref(),
            request.limit,
        );
        let value = self.service.signed_get(endpoint, &params).await?;
        parse_page(&value, parser)
    }

    async fn rewards(
        &mut self,
        request: &EarnRewardsRequest,
    ) -> Result<EarnPage<EarnReward>, IntegrationError> {
        request
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        let mut params = history_params(&request.window);
        params.push(("type", "ALL".into()));
        if let Some(asset) = &request.asset {
            params.push(("asset", asset.to_string()));
        }
        if let Some(product_id) = &request.product_id {
            params.push(("productId", product_id.clone()));
        }
        let value = self.service.signed_get(REWARD_HISTORY, &params).await?;
        parse_page(&value, parse_reward)
    }

    async fn rates(
        &mut self,
        request: &EarnRatesRequest,
    ) -> Result<EarnPage<EarnRateObservation>, IntegrationError> {
        request
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        let product_id = request.product_id.as_ref().ok_or_else(|| {
            IntegrationError::InvalidRequest("Binance rate history requires product_id".into())
        })?;
        let mut params = history_params(&request.window);
        params.push(("productId", product_id.clone()));
        let value = self.service.signed_get(RATE_HISTORY, &params).await?;
        parse_page(&value, |row| parse_rate(row, product_id))
    }

    async fn subscription_preview(
        &mut self,
        request: &EarnSubscriptionPreviewRequest,
    ) -> Result<EarnSubscriptionPreview, IntegrationError> {
        request
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        validate_account(self.descriptor(), &request.account)?;
        let product_value = self
            .service
            .signed_get(FLEXIBLE_LIST, &[("productId", request.product_id.clone())])
            .await?;
        let product = parse_page(&product_value, parse_product)?
            .items
            .into_iter()
            .find(|value| value.product_id == request.product_id)
            .ok_or_else(|| {
                IntegrationError::InvalidPayload("Binance Earn product not found".into())
            })?;
        let quota_value = self
            .service
            .signed_get(PERSONAL_QUOTA, &[("productId", request.product_id.clone())])
            .await?;
        let quota = decimal_field(&quota_value, "leftPersonalQuota").ok();
        let eligible = product.state == EarnProductState::Available
            && product
                .minimum_amount
                .is_none_or(|minimum| request.amount >= minimum)
            && quota.is_none_or(|left| request.amount <= left);
        Ok(EarnSubscriptionPreview {
            product_id: product.product_id,
            amount: request.amount,
            eligibility: if eligible {
                EarnSubscriptionEligibility::Eligible
            } else {
                EarnSubscriptionEligibility::Ineligible {
                    reason: "product state, minimum amount, or personal quota does not permit subscription".into(),
                }
            },
            rate_components: product.rate_components,
            remaining_subscription_quota: quota,
            liquidity: product.liquidity,
            redemption_options: product.redemption_options,
            observed_at_unix_nanos: now(),
        })
    }
}

impl EarnCommand for BinanceSimpleEarnRestConnection {
    async fn subscribe(
        &mut self,
        request: &EarnSubscribeRequest,
    ) -> crate::CommandResult<EarnSubmission> {
        request
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        validate_account(self.descriptor(), &request.account)?;
        let params = [
            ("productId", request.product_id.clone()),
            ("amount", request.amount.to_string()),
        ];
        map_submission(
            self.service.signed_post_command(SUBSCRIBE, &params).await?,
            "purchaseId",
        )
    }

    async fn redeem(
        &mut self,
        request: &EarnRedeemRequest,
    ) -> crate::CommandResult<EarnSubmission> {
        request
            .validate()
            .map_err(IntegrationError::InvalidRequest)?;
        validate_account(self.descriptor(), &request.account)?;
        if request.destination.is_some() {
            return Err(IntegrationError::UnsupportedOperation);
        }
        let mut params = vec![("productId", request.product_id.clone())];
        match request.amount {
            EarnRedemptionAmount::All => params.push(("redeemAll", "true".into())),
            EarnRedemptionAmount::Exact(amount) => params.push(("amount", amount.to_string())),
        }
        map_submission(
            self.service.signed_post_command(REDEEM, &params).await?,
            "redeemId",
        )
    }
}

impl EarnActionStatusQuery for BinanceSimpleEarnRestConnection {
    async fn action_status(
        &mut self,
        query: &EarnActionQuery,
    ) -> Result<Option<EarnActionStatus>, IntegrationError> {
        validate_account(self.descriptor(), &query.account)?;
        let Some(action_id) = query.participant_action_id.as_ref() else {
            return Err(IntegrationError::InvalidRequest(
                "Binance Earn reconciliation requires participant_action_id".into(),
            ));
        };
        let (path, field) = match query.action {
            EarnActionKind::Subscribe => (SUBSCRIPTION_HISTORY, "purchaseId"),
            EarnActionKind::Redeem => (REDEMPTION_HISTORY, "redeemId"),
        };
        let value = self
            .service
            .signed_get(path, &[(field, action_id.clone())])
            .await?;
        let rows = rows(&value)?;
        let mut matches = rows
            .iter()
            .filter(|row| scalar_string(row.get(field)).as_deref() == Some(action_id))
            .map(|row| parse_action_status(row, query))
            .collect::<Result<Vec<_>, _>>()?;
        match matches.len() {
            0 => Ok(None),
            1 => Ok(matches.pop()),
            _ => Err(IntegrationError::InvalidPayload(
                "Binance Earn history returned duplicate action identities".into(),
            )),
        }
    }
}

fn validate_account(
    descriptor: &ConnectionDescriptor,
    account: &crate::ExternalAccountIdentity,
) -> Result<(), IntegrationError> {
    if !account.broker.eq_ignore_ascii_case("binance")
        || descriptor.principal_id.as_deref() != Some(account.account_id.as_str())
    {
        return Err(IntegrationError::Authorization(
            "Binance Earn request does not match its credential-bound Account".into(),
        ));
    }
    Ok(())
}

fn map_submission(
    outcome: CommandOutcome<Value>,
    id_field: &str,
) -> crate::CommandResult<EarnSubmission> {
    match outcome {
        CommandOutcome::Confirmed(value)
            if value.get("success").and_then(Value::as_bool) == Some(false) =>
        {
            Ok(CommandOutcome::Rejected(ParticipantRejection::new(
                value
                    .get("msg")
                    .and_then(Value::as_str)
                    .unwrap_or("Binance Earn command rejected"),
            )))
        },
        CommandOutcome::Confirmed(value) => Ok(CommandOutcome::Confirmed(EarnSubmission {
            participant_action_id: scalar_string(value.get(id_field)),
            acknowledged_at_unix_nanos: Some(now()),
        })),
        CommandOutcome::Rejected(value) => Ok(CommandOutcome::Rejected(value)),
        CommandOutcome::Indeterminate(value) => Ok(CommandOutcome::Indeterminate(value)),
    }
}

fn parse_product(row: &Value) -> Result<EarnProduct, IntegrationError> {
    let state = match row.get("status").and_then(Value::as_str) {
        Some("PURCHASING") | Some("AVAILABLE")
            if row.get("canPurchase").and_then(Value::as_bool) != Some(false) =>
        {
            EarnProductState::Available
        },
        Some("SUSPENDED") => EarnProductState::Suspended,
        Some("END") | Some("CLOSED") => EarnProductState::Closed,
        Some(value) => EarnProductState::Unknown(value.into()),
        None if row.get("canPurchase").and_then(Value::as_bool) == Some(true) => {
            EarnProductState::Available
        },
        None => EarnProductState::Unknown("missing".into()),
    };
    let rate_components = row
        .get("latestAnnualPercentageRate")
        .and_then(Value::as_str)
        .map(|value| {
            parse_rate_value(value).map(|annual_rate| EarnRateComponent {
                kind: EarnRateComponentKind::Realtime,
                annual_rate,
                rate_kind: EarnRateKind::Apr,
                eligible_amount_limit: None,
                valid_from_unix_nanos: None,
                valid_until_unix_nanos: None,
            })
        })
        .transpose()?
        .into_iter()
        .collect();
    Ok(EarnProduct {
        product_id: text(row, "productId")?.into(),
        asset: kairos_primitives::reference::Currency::new(text(row, "asset")?).map_err(payload)?,
        family: EarnProductFamily::Flexible,
        participant_product_type: "FLEXIBLE".into(),
        rate_components,
        minimum_amount: optional_decimal(row, "minPurchaseAmount")?,
        maximum_amount: None,
        remaining_subscription_quota: None,
        liquidity: EarnLiquidity::Immediate,
        redemption_options: vec![EarnRedemptionOption {
            channel: EarnRedemptionChannel::Immediate,
            settlement_delay_seconds: None,
            remaining_quota: None,
            forfeits_accrued_rewards: None,
        }],
        state,
    })
}

fn parse_position(row: &Value) -> Result<EarnPosition, IntegrationError> {
    let principal = decimal_field(row, "totalAmount")?;
    let redeeming = optional_decimal(row, "redeemingAmount")?.unwrap_or(Quantity::ZERO);
    Ok(EarnPosition {
        participant_position_id: scalar_string(row.get("positionId")),
        product_id: text(row, "productId")?.into(),
        asset: kairos_primitives::reference::Currency::new(text(row, "asset")?).map_err(payload)?,
        family: EarnProductFamily::Flexible,
        principal,
        accrued_rewards: Vec::new(),
        redeemable_amount: optional_decimal(row, "freeAmount")?.or(Some(principal)),
        subscribed_at_unix_nanos: None,
        matures_at_unix_nanos: None,
        state: if redeeming > Quantity::ZERO {
            EarnPositionState::Redeeming
        } else if principal == Quantity::ZERO {
            EarnPositionState::Redeemed
        } else {
            EarnPositionState::Active
        },
        observed_at_unix_nanos: Some(now()),
    })
}

fn parse_locked_position(row: &Value) -> Result<EarnPosition, IntegrationError> {
    let principal = decimal_field(row, "amount")?;
    let reward_asset = text(row, "rewardAsset")?;
    let mut accrued_rewards = Vec::new();
    if let Some(amount) = optional_decimal(row, "rewardAmt")? {
        accrued_rewards.push(crate::EarnAccruedReward {
            asset: kairos_primitives::reference::Currency::new(reward_asset).map_err(payload)?,
            amount,
            component: Some(EarnRateComponentKind::Base),
        });
    }
    if let (Some(asset), Some(amount)) = (
        row.get("boostRewardAsset").and_then(Value::as_str),
        optional_decimal(row, "totalBoostRewardAmt")?,
    ) {
        accrued_rewards.push(crate::EarnAccruedReward {
            asset: kairos_primitives::reference::Currency::new(asset).map_err(payload)?,
            amount,
            component: Some(EarnRateComponentKind::Promotional),
        });
    }
    let participant_state = row
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or("UNKNOWN");
    let state = match participant_state.to_ascii_uppercase().as_str() {
        "HOLDING" | "ACTIVE" => EarnPositionState::Active,
        "REDEEMING" | "REDEEMING_EARLY" => EarnPositionState::Redeeming,
        "REDEEMED" | "CLOSED" => EarnPositionState::Redeemed,
        _ => EarnPositionState::Unknown(participant_state.into()),
    };
    Ok(EarnPosition {
        participant_position_id: scalar_string(row.get("positionId")),
        product_id: text(row, "projectId")?.into(),
        asset: kairos_primitives::reference::Currency::new(text(row, "asset")?).map_err(payload)?,
        family: EarnProductFamily::Locked,
        principal,
        accrued_rewards,
        redeemable_amount: optional_decimal(row, "redeemAmountEarly")?,
        subscribed_at_unix_nanos: timestamp(row, &["purchaseTime"]),
        matures_at_unix_nanos: timestamp(row, &["deliverDate", "rewardsEndDate"]),
        state,
        observed_at_unix_nanos: Some(now()),
    })
}

fn parse_reward(row: &Value) -> Result<EarnReward, IntegrationError> {
    let amount = text(row, "rewards")?
        .parse::<SignedQuantity>()
        .map_err(payload)?;
    Ok(EarnReward {
        participant_reward_id: scalar_string(row.get("id")),
        product_id: scalar_string(row.get("productId")),
        asset: kairos_primitives::reference::Currency::new(text(row, "asset")?).map_err(payload)?,
        amount,
        occurred_at_unix_nanos: timestamp(row, &["time", "createTime"]),
    })
}

fn parse_rate(row: &Value, product_id: &str) -> Result<EarnRateObservation, IntegrationError> {
    Ok(EarnRateObservation {
        product_id: product_id.into(),
        annual_rate: parse_rate_value(text(row, "annualPercentageRate")?)?,
        rate_kind: EarnRateKind::Apr,
        observed_at_unix_nanos: timestamp(row, &["time", "createTime"]).unwrap_or_else(now),
    })
}

fn parse_action_status(
    row: &Value,
    query: &EarnActionQuery,
) -> Result<EarnActionStatus, IntegrationError> {
    let participant_state = text(row, "status")?.to_owned();
    let state = match participant_state.as_str() {
        "PENDING" | "PROCESSING" => EarnActionState::Pending,
        "SUCCESS" | "SUCCEEDED" => EarnActionState::Succeeded,
        "FAILED" | "FAILURE" => EarnActionState::Failed,
        _ => EarnActionState::Unknown,
    };
    Ok(EarnActionStatus {
        idempotency_key: query.idempotency_key.clone(),
        participant_action_id: query.participant_action_id.clone(),
        action: query.action,
        state,
        participant_state: Some(participant_state),
        updated_at_unix_nanos: timestamp(row, &["time", "createTime"]),
        failure_reason: row.get("msg").and_then(Value::as_str).map(str::to_owned),
    })
}

fn parse_page<T>(
    value: &Value,
    parse: impl Fn(&Value) -> Result<T, IntegrationError>,
) -> Result<EarnPage<T>, IntegrationError> {
    let items = rows(value)?
        .iter()
        .map(parse)
        .collect::<Result<Vec<_>, _>>()?;
    let current = value.get("current").and_then(Value::as_u64).unwrap_or(1);
    let size = value
        .get("size")
        .and_then(Value::as_u64)
        .unwrap_or(items.len() as u64);
    let total = value.get("total").and_then(Value::as_u64);
    Ok(EarnPage {
        next_cursor: total
            .is_some_and(|total| current.saturating_mul(size) < total)
            .then(|| current.saturating_add(1).to_string()),
        items,
    })
}

fn rows(value: &Value) -> Result<&[Value], IntegrationError> {
    value
        .get("rows")
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .ok_or_else(|| IntegrationError::InvalidPayload("Binance Earn rows are missing".into()))
}

fn page_params(
    asset: Option<String>,
    product_id: Option<String>,
    cursor: Option<&str>,
    limit: Option<u16>,
) -> Vec<(&'static str, String)> {
    let mut params = Vec::new();
    if let Some(asset) = asset {
        params.push(("asset", asset));
    }
    if let Some(product_id) = product_id {
        params.push(("productId", product_id));
    }
    if let Some(current) = cursor {
        params.push(("current", current.into()));
    }
    if let Some(size) = limit {
        params.push(("size", size.to_string()));
    }
    params
}

fn position_page_params(
    asset: Option<String>,
    product_field: &'static str,
    product_id: Option<String>,
    cursor: Option<&str>,
    limit: Option<u16>,
) -> Vec<(&'static str, String)> {
    let mut params = page_params(asset, None, cursor, limit);
    if let Some(product_id) = product_id {
        params.push((product_field, product_id));
    }
    params
}

fn history_params(window: &crate::EarnHistoryWindow) -> Vec<(&'static str, String)> {
    let mut params = page_params(None, None, window.cursor.as_deref(), window.limit);
    if let Some(start) = window.start_at_unix_nanos {
        params.push(("startTime", (start.get() / 1_000_000).to_string()));
    }
    if let Some(end) = window.end_at_unix_nanos {
        params.push(("endTime", (end.get() / 1_000_000).to_string()));
    }
    params
}

fn require_flexible(family: Option<&EarnProductFamily>) -> Result<(), IntegrationError> {
    if family.is_some_and(|value| value != &EarnProductFamily::Flexible) {
        return Err(IntegrationError::UnsupportedOperation);
    }
    Ok(())
}

fn text<'a>(value: &'a Value, field: &str) -> Result<&'a str, IntegrationError> {
    value
        .get(field)
        .and_then(Value::as_str)
        .ok_or_else(|| IntegrationError::InvalidPayload(format!("Binance Earn {field} is missing")))
}

fn scalar_string(value: Option<&Value>) -> Option<String> {
    match value? {
        Value::String(value) if !value.is_empty() => Some(value.clone()),
        Value::Number(value) => Some(value.to_string()),
        _ => None,
    }
}

fn decimal_field(value: &Value, field: &str) -> Result<Quantity, IntegrationError> {
    text(value, field)?.parse().map_err(payload)
}

fn optional_decimal(value: &Value, field: &str) -> Result<Option<Quantity>, IntegrationError> {
    value
        .get(field)
        .and_then(Value::as_str)
        .map(|value| value.parse().map_err(payload))
        .transpose()
}

fn parse_rate_value(value: &str) -> Result<Rate, IntegrationError> {
    Rate::from_str(value).map_err(payload)
}

fn timestamp(value: &Value, fields: &[&str]) -> Option<UnixNanos> {
    fields
        .iter()
        .find_map(|field| value.get(*field).and_then(Value::as_u64))
        .and_then(|millis| millis.checked_mul(1_000_000))
        .map(UnixNanos::new)
}

fn now() -> UnixNanos {
    UnixNanos::new(
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
            .min(u128::from(u64::MAX)) as u64,
    )
}

fn payload(error: impl std::fmt::Display) -> IntegrationError {
    IntegrationError::InvalidPayload(error.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_flexible_product_and_preserves_unknown_state() {
        let product = parse_product(&serde_json::json!({
            "productId":"USDT001", "asset":"USDT", "latestAnnualPercentageRate":"0.025",
            "minPurchaseAmount":"0.1", "canPurchase":true, "status":"NEW_PROVIDER_STATE"
        }))
        .unwrap();
        assert_eq!(product.product_id, "USDT001");
        assert_eq!(product.rate_components.len(), 1);
        assert_eq!(
            product.state,
            EarnProductState::Unknown("NEW_PROVIDER_STATE".into())
        );
    }

    #[test]
    fn parses_position_without_mixing_it_with_trade_positions() {
        let position = parse_position(&serde_json::json!({
            "productId":"USDT001", "asset":"USDT", "totalAmount":"125.5",
            "freeAmount":"120.5", "redeemingAmount":"5"
        }))
        .unwrap();
        assert_eq!(position.principal, "125.5".parse().unwrap());
        assert_eq!(position.state, EarnPositionState::Redeeming);
    }

    #[test]
    fn parses_locked_position_with_maturity_and_rewards() {
        let position = parse_locked_position(&serde_json::json!({
            "positionId": 347608038,
            "projectId": "Bnb*120",
            "asset": "BNB",
            "amount": "0.05",
            "purchaseTime": 1783439066000_u64,
            "rewardAsset": "BNB",
            "rewardAmt": "0.001",
            "redeemAmountEarly": "0.05",
            "rewardsEndDate": 1793836800000_u64,
            "deliverDate": 1793959200000_u64,
            "status": "HOLDING",
            "boostRewardAsset": "USDT",
            "totalBoostRewardAmt": "0.25"
        }))
        .unwrap();

        assert_eq!(
            position.participant_position_id.as_deref(),
            Some("347608038")
        );
        assert_eq!(position.product_id, "Bnb*120");
        assert_eq!(position.family, EarnProductFamily::Locked);
        assert_eq!(position.principal, "0.05".parse().unwrap());
        assert_eq!(position.state, EarnPositionState::Active);
        assert_eq!(position.accrued_rewards.len(), 2);
        assert_eq!(
            position.matures_at_unix_nanos,
            Some(UnixNanos::new(1_793_959_200_000_000_000))
        );
    }

    #[test]
    fn unknown_action_status_is_not_guessed_as_success() {
        let query = EarnActionQuery {
            account: crate::ExternalAccountIdentity::new("binance", "account-1").unwrap(),
            account_segment: crate::ExternalAccountSegment {
                identity: crate::ExternalAccountIdentity::new("binance", "account-1").unwrap(),
                segment_key: kairos_primitives::account::SegmentKey::new("spot").unwrap(),
                environment: "live".into(),
                account_model: None,
            },
            asset: kairos_primitives::reference::Currency::new("USDT").unwrap(),
            product_id: "USDT001".into(),
            idempotency_key: kairos_primitives::runtime::IdempotencyKey::new("capital:1:redeem")
                .unwrap(),
            participant_action_id: Some("42".into()),
            action: EarnActionKind::Redeem,
        };
        let status =
            parse_action_status(&serde_json::json!({"status":"REVIEWING", "time":1}), &query)
                .unwrap();
        assert_eq!(status.state, EarnActionState::Unknown);
        assert_eq!(status.participant_state.as_deref(), Some("REVIEWING"));
    }

    #[test]
    fn pagination_cursor_is_opaque_to_callers() {
        let page = parse_page(&serde_json::json!({
            "rows":[{"productId":"USDT001", "asset":"USDT", "totalAmount":"1", "redeemingAmount":"0"}],
            "current":2, "size":1, "total":3
        }), parse_position).unwrap();
        assert_eq!(page.next_cursor.as_deref(), Some("3"));
    }
}
