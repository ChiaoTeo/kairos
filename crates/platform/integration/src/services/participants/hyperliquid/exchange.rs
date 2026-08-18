use ethers::signers::LocalWallet;
use hyperliquid_rust_sdk::{
    BaseUrl, ClientCancelRequest, ClientLimit, ClientOrder, ClientOrderRequest, ExchangeClient,
    ExchangeDataStatus, ExchangeResponseStatus,
};
use kairos_primitives::{RemoteOrderId, UnixNanos};

use crate::{
    CommandOutcome, CommandResult, DecimalValue, IndeterminateCommand, IntegrationError,
    OrderEntryEvent, OrderEntryRequest, OrderEntryStatus, OrderType, ParticipantRejection,
    TimeInForce,
};

pub(crate) struct ExchangeService {
    base_url: BaseUrl,
    wallet: LocalWallet,
    client: Option<ExchangeClient>,
}

impl ExchangeService {
    pub(crate) fn new(endpoint: &str, private_key: &str) -> Result<Self, IntegrationError> {
        let base_url = match endpoint.trim_end_matches('/') {
            "https://api.hyperliquid.xyz" => BaseUrl::Mainnet,
            "https://api.hyperliquid-testnet.xyz" => BaseUrl::Testnet,
            "http://localhost:3001" => BaseUrl::Localhost,
            _ => {
                return Err(IntegrationError::InvalidRequest(
                    "Hyperliquid SDK supports mainnet, testnet, or localhost endpoints".into(),
                ))
            }
        };
        let wallet = private_key.parse::<LocalWallet>().map_err(|error| {
            IntegrationError::Authentication(format!("invalid Hyperliquid private key: {error}"))
        })?;
        Ok(Self {
            base_url,
            wallet,
            client: None,
        })
    }

    pub(crate) fn is_connected(&self) -> bool {
        self.client.is_some()
    }

    pub(crate) async fn connect(&mut self) -> Result<(), IntegrationError> {
        self.client = Some(
            ExchangeClient::new(None, self.wallet.clone(), Some(self.base_url), None, None)
                .await
                .map_err(|error| IntegrationError::Transport(error.to_string()))?,
        );
        Ok(())
    }

    pub(crate) fn disconnect(&mut self) {
        self.client = None;
    }

    pub(crate) async fn submit(
        &mut self,
        request: &OrderEntryRequest,
    ) -> CommandResult<OrderEntryEvent> {
        if request.order_type != OrderType::Limit {
            return Err(IntegrationError::UnsupportedOperation);
        }
        let limit_price = request.limit_price.ok_or_else(|| {
            IntegrationError::InvalidRequest("Hyperliquid limit price is required".into())
        })?;
        let tif = match request
            .options
            .time_in_force
            .unwrap_or(TimeInForce::GoodTilCanceled)
        {
            TimeInForce::GoodTilCanceled => {
                if request.options.post_only == Some(true) {
                    "Alo"
                } else {
                    "Gtc"
                }
            }
            TimeInForce::ImmediateOrCancel | TimeInForce::FillOrKill => "Ioc",
            TimeInForce::Day => return Err(IntegrationError::UnsupportedOperation),
        };
        let order = ClientOrderRequest {
            asset: request.participant_instrument.source_symbol.to_string(),
            is_buy: request.side == crate::OrderSide::Buy,
            reduce_only: request.options.reduce_only.unwrap_or(false),
            limit_px: decimal(limit_price),
            sz: decimal(request.quantity),
            cloid: Some(uuid::Uuid::new_v5(
                &uuid::Uuid::NAMESPACE_OID,
                request.order_id.to_string().as_bytes(),
            )),
            order_type: ClientOrder::Limit(ClientLimit { tif: tif.into() }),
        };
        let response = match self.client_mut()?.order(order, None).await {
            Ok(response) => response,
            Err(error) => {
                return Ok(CommandOutcome::Indeterminate(
                    IndeterminateCommand::may_have_been_sent(error.to_string()),
                ))
            }
        };
        normalize_response(request, response, OrderEntryStatus::Accepted)
    }

    pub(crate) async fn cancel(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> CommandResult<OrderEntryEvent> {
        let oid = remote_order_id
            .strip_prefix("hyperliquid:")
            .unwrap_or(remote_order_id)
            .parse::<u64>()
            .map_err(|_| IntegrationError::InvalidRequest("invalid Hyperliquid order id".into()))?;
        let cancel = ClientCancelRequest {
            asset: request.participant_instrument.source_symbol.to_string(),
            oid,
        };
        let response = match self.client_mut()?.cancel(cancel, None).await {
            Ok(response) => response,
            Err(error) => {
                return Ok(CommandOutcome::Indeterminate(
                    IndeterminateCommand::may_have_been_sent(error.to_string()),
                ))
            }
        };
        match response {
            ExchangeResponseStatus::Err(message) => Ok(rejected(message)),
            ExchangeResponseStatus::Ok(response) => {
                if let Some(ExchangeDataStatus::Error(message)) = response
                    .data
                    .and_then(|data| data.statuses.into_iter().next())
                {
                    return Ok(rejected(message));
                }
                Ok(CommandOutcome::Confirmed(OrderEntryEvent {
                    order_id: request.order_id.clone(),
                    status: OrderEntryStatus::Canceled,
                    remote_order_id: RemoteOrderId::new(format!("hyperliquid:{oid}")).ok(),
                    filled_quantity: None,
                    occurred_at_unix_nanos: UnixNanos::from(at_unix_nanos),
                    reason: String::new(),
                }))
            }
        }
    }

    fn client_mut(&mut self) -> Result<&mut ExchangeClient, IntegrationError> {
        self.client.as_mut().ok_or(IntegrationError::NotReady)
    }
}

fn normalize_response(
    request: &OrderEntryRequest,
    response: ExchangeResponseStatus,
    default_status: OrderEntryStatus,
) -> CommandResult<OrderEntryEvent> {
    let response = match response {
        ExchangeResponseStatus::Err(message) => return Ok(rejected(message)),
        ExchangeResponseStatus::Ok(response) => response,
    };
    let status = response
        .data
        .and_then(|data| data.statuses.into_iter().next())
        .ok_or_else(|| {
            IntegrationError::InvalidPayload("Hyperliquid exchange response has no status".into())
        })?;
    let (status, oid, filled) = match status {
        ExchangeDataStatus::Error(message) => return Ok(rejected(message)),
        ExchangeDataStatus::Resting(order) => (default_status, Some(order.oid), None),
        ExchangeDataStatus::Filled(order) => (
            OrderEntryStatus::Filled,
            Some(order.oid),
            parse_decimal(&order.total_sz).ok(),
        ),
        ExchangeDataStatus::Success
        | ExchangeDataStatus::WaitingForFill
        | ExchangeDataStatus::WaitingForTrigger => (default_status, None, None),
    };
    Ok(CommandOutcome::Confirmed(OrderEntryEvent {
        order_id: request.order_id.clone(),
        status,
        remote_order_id: oid.and_then(|oid| RemoteOrderId::new(format!("hyperliquid:{oid}")).ok()),
        filled_quantity: filled,
        occurred_at_unix_nanos: now(),
        reason: String::new(),
    }))
}

fn rejected<T>(message: String) -> CommandOutcome<T> {
    CommandOutcome::Rejected(ParticipantRejection {
        code: None,
        message,
        participant_request_id: None,
    })
}

fn decimal(value: DecimalValue) -> f64 {
    value.mantissa as f64 / 10_f64.powi(i32::from(value.scale))
}

fn parse_decimal(value: &str) -> Result<DecimalValue, IntegrationError> {
    let value = value
        .parse::<rust_decimal::Decimal>()
        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?;
    Ok(DecimalValue::new(
        i64::try_from(value.mantissa())
            .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
        u8::try_from(value.scale())
            .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?,
    ))
}

fn now() -> UnixNanos {
    use std::time::{SystemTime, UNIX_EPOCH};
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_nanos())
        .unwrap_or_default();
    UnixNanos::from(u64::try_from(nanos).unwrap_or(u64::MAX))
}
