use serde_json::Value;

use crate::{CommandOutcome, CommandResult, IntegrationError, ParticipantRejection};

rest_connection!(BinanceAlgoTradingRestConnection, "advanced.algo.rest");

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BinanceAlgoFamily {
    Spot,
    Futures,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BinanceTwapOrderRequest {
    pub symbol: String,
    pub side: crate::OrderSide,
    pub quantity: String,
    pub duration_seconds: u32,
    pub limit_price: Option<String>,
    pub client_algo_id: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BinanceVolumeParticipationOrderRequest {
    pub symbol: String,
    pub side: crate::OrderSide,
    pub quantity: String,
    pub urgency: String,
    pub client_algo_id: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct BinanceAlgoOrder {
    pub algo_id: String,
    pub client_algo_id: Option<String>,
    pub symbol: Option<String>,
    pub status: Option<String>,
}

impl BinanceAlgoTradingRestConnection {
    pub async fn submit_twap(
        &mut self,
        family: BinanceAlgoFamily,
        request: &BinanceTwapOrderRequest,
    ) -> CommandResult<BinanceAlgoOrder> {
        validate_twap(request)?;
        let mut params = vec![
            ("symbol", request.symbol.clone()),
            ("side", side(request.side).into()),
            ("quantity", request.quantity.clone()),
            ("duration", request.duration_seconds.to_string()),
        ];
        if let Some(value) = &request.limit_price {
            params.push(("limitPrice", value.clone()));
        }
        if let Some(value) = &request.client_algo_id {
            params.push(("clientAlgoId", value.clone()));
        }
        let outcome = self
            .service
            .signed_post_command(
                match family {
                    BinanceAlgoFamily::Spot => "/sapi/v1/algo/spot/newOrderTwap",
                    BinanceAlgoFamily::Futures => "/sapi/v1/algo/futures/newOrderTwap",
                },
                &params,
            )
            .await?;
        normalize_command(outcome)
    }

    pub async fn submit_futures_volume_participation(
        &mut self,
        request: &BinanceVolumeParticipationOrderRequest,
    ) -> CommandResult<BinanceAlgoOrder> {
        require("symbol", &request.symbol)?;
        require("quantity", &request.quantity)?;
        require("urgency", &request.urgency)?;
        let mut params = vec![
            ("symbol", request.symbol.clone()),
            ("side", side(request.side).into()),
            ("quantity", request.quantity.clone()),
            ("urgency", request.urgency.clone()),
        ];
        if let Some(value) = &request.client_algo_id {
            params.push(("clientAlgoId", value.clone()));
        }
        let outcome = self
            .service
            .signed_post_command("/sapi/v1/algo/futures/newOrderVp", &params)
            .await?;
        normalize_command(outcome)
    }

    pub async fn cancel(
        &mut self,
        family: BinanceAlgoFamily,
        algo_id: &str,
    ) -> CommandResult<BinanceAlgoOrder> {
        require("algo id", algo_id)?;
        let outcome = self
            .service
            .signed_delete_command(
                match family {
                    BinanceAlgoFamily::Spot => "/sapi/v1/algo/spot/order",
                    BinanceAlgoFamily::Futures => "/sapi/v1/algo/futures/order",
                },
                &[("algoId", algo_id.into())],
            )
            .await?;
        normalize_command(outcome)
    }

    pub async fn open_orders(
        &mut self,
        family: BinanceAlgoFamily,
    ) -> Result<Vec<BinanceAlgoOrder>, IntegrationError> {
        self.query(family, "openOrders").await
    }
    pub async fn historical_orders(
        &mut self,
        family: BinanceAlgoFamily,
    ) -> Result<Vec<BinanceAlgoOrder>, IntegrationError> {
        self.query(family, "historicalOrders").await
    }

    async fn query(
        &mut self,
        family: BinanceAlgoFamily,
        operation: &str,
    ) -> Result<Vec<BinanceAlgoOrder>, IntegrationError> {
        let path = format!(
            "/sapi/v1/algo/{}/{operation}",
            match family {
                BinanceAlgoFamily::Spot => "spot",
                BinanceAlgoFamily::Futures => "futures",
            }
        );
        let value = self.service.signed_get(&path, &[]).await?;
        normalize_orders(&value)
    }
}

fn normalize_command(outcome: CommandOutcome<Value>) -> CommandResult<BinanceAlgoOrder> {
    Ok(match outcome {
        CommandOutcome::Confirmed(value) => {
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
                        .unwrap_or("Binance Algo rejected command")
                        .into(),
                    participant_request_id: None,
                })
            } else {
                CommandOutcome::Confirmed(normalize_order(value.get("data").unwrap_or(&value))?)
            }
        }
        CommandOutcome::Rejected(error) => CommandOutcome::Rejected(error),
        CommandOutcome::Indeterminate(error) => CommandOutcome::Indeterminate(error),
    })
}
fn normalize_orders(value: &Value) -> Result<Vec<BinanceAlgoOrder>, IntegrationError> {
    value
        .get("data")
        .unwrap_or(value)
        .as_array()
        .ok_or_else(|| {
            IntegrationError::InvalidPayload("Binance Algo order list must be an array".into())
        })?
        .iter()
        .map(normalize_order)
        .collect()
}
fn normalize_order(value: &Value) -> Result<BinanceAlgoOrder, IntegrationError> {
    Ok(BinanceAlgoOrder {
        algo_id: field(value, &["algoId"])
            .ok_or_else(|| IntegrationError::InvalidPayload("Binance Algo id is missing".into()))?,
        client_algo_id: field(value, &["clientAlgoId"]),
        symbol: field(value, &["symbol"]),
        status: field(value, &["algoStatus", "status"]),
    })
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
fn validate_twap(request: &BinanceTwapOrderRequest) -> Result<(), IntegrationError> {
    require("symbol", &request.symbol)?;
    require("quantity", &request.quantity)?;
    if request.duration_seconds == 0 {
        return Err(IntegrationError::InvalidRequest(
            "Binance Algo duration must be positive".into(),
        ));
    }
    Ok(())
}
fn require(name: &str, value: &str) -> Result<(), IntegrationError> {
    if value.trim().is_empty() {
        Err(IntegrationError::InvalidRequest(format!(
            "Binance Algo {name} is required"
        )))
    } else {
        Ok(())
    }
}
fn side(value: crate::OrderSide) -> &'static str {
    if value == crate::OrderSide::Buy {
        "BUY"
    } else {
        "SELL"
    }
}
