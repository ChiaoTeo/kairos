use ethers::signers::LocalWallet;
use hyperliquid_rust_sdk::{
    BaseUrl, ClientCancelRequest, ClientLimit, ClientModifyRequest, ClientOrder,
    ClientOrderRequest, ExchangeClient, ExchangeDataStatus, ExchangeResponseStatus,
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
                ));
            },
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
        let order = client_order(request)?;
        let response = match self.client_mut()?.order(order, None).await {
            Ok(response) => response,
            Err(error) => {
                return Ok(CommandOutcome::Indeterminate(
                    IndeterminateCommand::may_have_been_sent(error.to_string()),
                ));
            },
        };
        normalize_response(request, response, OrderEntryStatus::Accepted)
    }

    pub(crate) async fn submit_batch(
        &mut self,
        requests: &[OrderEntryRequest],
    ) -> CommandResult<Vec<CommandOutcome<OrderEntryEvent>>> {
        validate_batch(requests.len())?;
        let orders = requests
            .iter()
            .map(client_order)
            .collect::<Result<Vec<_>, _>>()?;
        let response = match self.client_mut()?.bulk_order(orders, None).await {
            Ok(response) => response,
            Err(error) => {
                return Ok(CommandOutcome::Indeterminate(
                    IndeterminateCommand::may_have_been_sent(error.to_string()),
                ));
            },
        };
        normalize_batch_response(requests, response, OrderEntryStatus::Accepted)
    }

    pub(crate) async fn modify(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
    ) -> CommandResult<OrderEntryEvent> {
        let modify = ClientModifyRequest {
            oid: parse_oid(remote_order_id)?,
            order: client_order(request)?,
        };
        let response = match self.client_mut()?.modify(modify, None).await {
            Ok(response) => response,
            Err(error) => {
                return Ok(CommandOutcome::Indeterminate(
                    IndeterminateCommand::may_have_been_sent(error.to_string()),
                ));
            },
        };
        normalize_response(request, response, OrderEntryStatus::Accepted)
    }

    pub(crate) async fn modify_batch(
        &mut self,
        requests: &[(OrderEntryRequest, String)],
    ) -> CommandResult<Vec<CommandOutcome<OrderEntryEvent>>> {
        validate_batch(requests.len())?;
        let modifies = requests
            .iter()
            .map(|(request, order_id)| {
                Ok(ClientModifyRequest {
                    oid: parse_oid(order_id)?,
                    order: client_order(request)?,
                })
            })
            .collect::<Result<Vec<_>, IntegrationError>>()?;
        let response = match self.client_mut()?.bulk_modify(modifies, None).await {
            Ok(response) => response,
            Err(error) => {
                return Ok(CommandOutcome::Indeterminate(
                    IndeterminateCommand::may_have_been_sent(error.to_string()),
                ));
            },
        };
        let orders = requests
            .iter()
            .map(|(request, _)| request.clone())
            .collect::<Vec<_>>();
        normalize_batch_response(&orders, response, OrderEntryStatus::Accepted)
    }

    pub(crate) async fn cancel_batch(
        &mut self,
        requests: &[(OrderEntryRequest, String, u64)],
    ) -> CommandResult<Vec<CommandOutcome<OrderEntryEvent>>> {
        validate_batch(requests.len())?;
        let cancels = requests
            .iter()
            .map(|(request, order_id, _)| {
                Ok(ClientCancelRequest {
                    asset: request.participant_instrument.source_symbol.to_string(),
                    oid: parse_oid(order_id)?,
                })
            })
            .collect::<Result<Vec<_>, IntegrationError>>()?;
        let response = match self.client_mut()?.bulk_cancel(cancels, None).await {
            Ok(response) => response,
            Err(error) => {
                return Ok(CommandOutcome::Indeterminate(
                    IndeterminateCommand::may_have_been_sent(error.to_string()),
                ));
            },
        };
        normalize_cancel_batch(requests, response)
    }

    pub(crate) async fn cancel(
        &mut self,
        request: &OrderEntryRequest,
        remote_order_id: &str,
        at_unix_nanos: u64,
    ) -> CommandResult<OrderEntryEvent> {
        let oid = parse_oid(remote_order_id)?;
        let cancel = ClientCancelRequest {
            asset: request.participant_instrument.source_symbol.to_string(),
            oid,
        };
        let response = match self.client_mut()?.cancel(cancel, None).await {
            Ok(response) => response,
            Err(error) => {
                return Ok(CommandOutcome::Indeterminate(
                    IndeterminateCommand::may_have_been_sent(error.to_string()),
                ));
            },
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
            },
        }
    }

    fn client_mut(&mut self) -> Result<&mut ExchangeClient, IntegrationError> {
        self.client.as_mut().ok_or(IntegrationError::NotReady)
    }
}

fn client_order(request: &OrderEntryRequest) -> Result<ClientOrderRequest, IntegrationError> {
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
        },
        TimeInForce::ImmediateOrCancel | TimeInForce::FillOrKill => "Ioc",
        TimeInForce::Day => return Err(IntegrationError::UnsupportedOperation),
    };
    Ok(ClientOrderRequest {
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
    })
}

fn parse_oid(remote_order_id: &str) -> Result<u64, IntegrationError> {
    remote_order_id
        .strip_prefix("hyperliquid:")
        .unwrap_or(remote_order_id)
        .parse::<u64>()
        .map_err(|_| IntegrationError::InvalidRequest("invalid Hyperliquid order id".into()))
}

fn validate_batch(length: usize) -> Result<(), IntegrationError> {
    if length == 0 || length > 1_000 {
        return Err(IntegrationError::InvalidRequest(
            "Hyperliquid batch must contain 1-1000 items".into(),
        ));
    }
    Ok(())
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
    normalize_status(request, status, default_status)
}

fn normalize_status(
    request: &OrderEntryRequest,
    status: ExchangeDataStatus,
    default_status: OrderEntryStatus,
) -> CommandResult<OrderEntryEvent> {
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

fn normalize_batch_response(
    requests: &[OrderEntryRequest],
    response: ExchangeResponseStatus,
    default_status: OrderEntryStatus,
) -> CommandResult<Vec<CommandOutcome<OrderEntryEvent>>> {
    let response = match response {
        ExchangeResponseStatus::Err(message) => return Ok(rejected(message)),
        ExchangeResponseStatus::Ok(response) => response,
    };
    let mut statuses = response
        .data
        .map(|data| data.statuses.into_iter())
        .into_iter()
        .flatten();
    let outcomes = requests
        .iter()
        .enumerate()
        .map(|(index, request)| match statuses.next() {
            Some(status) => normalize_status(request, status, default_status),
            None => Ok(CommandOutcome::Indeterminate(
                IndeterminateCommand::may_have_been_sent(format!(
                    "Hyperliquid batch response item {index} is missing"
                )),
            )),
        })
        .collect::<Result<Vec<_>, _>>()?;
    Ok(CommandOutcome::Confirmed(outcomes))
}

fn normalize_cancel_batch(
    requests: &[(OrderEntryRequest, String, u64)],
    response: ExchangeResponseStatus,
) -> CommandResult<Vec<CommandOutcome<OrderEntryEvent>>> {
    let response = match response {
        ExchangeResponseStatus::Err(message) => return Ok(rejected(message)),
        ExchangeResponseStatus::Ok(response) => response,
    };
    let mut statuses = response
        .data
        .map(|data| data.statuses.into_iter())
        .into_iter()
        .flatten();
    let outcomes = requests
        .iter()
        .enumerate()
        .map(
            |(index, (request, order_id, at_unix_nanos))| match statuses.next() {
                Some(ExchangeDataStatus::Error(message)) => Ok(rejected(message)),
                Some(_) => Ok(CommandOutcome::Confirmed(OrderEntryEvent {
                    order_id: request.order_id.clone(),
                    status: OrderEntryStatus::Canceled,
                    remote_order_id: RemoteOrderId::new(format!(
                        "hyperliquid:{}",
                        parse_oid(order_id)?
                    ))
                    .ok(),
                    filled_quantity: None,
                    occurred_at_unix_nanos: UnixNanos::from(*at_unix_nanos),
                    reason: String::new(),
                })),
                None => Ok(CommandOutcome::Indeterminate(
                    IndeterminateCommand::may_have_been_sent(format!(
                        "Hyperliquid batch cancel response item {index} is missing"
                    )),
                )),
            },
        )
        .collect::<Result<Vec<_>, IntegrationError>>()?;
    Ok(CommandOutcome::Confirmed(outcomes))
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
    DecimalValue::parse(value).map_err(IntegrationError::InvalidPayload)
}

fn now() -> UnixNanos {
    use std::time::{SystemTime, UNIX_EPOCH};
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_nanos())
        .unwrap_or_default();
    UnixNanos::from(u64::try_from(nanos).unwrap_or(u64::MAX))
}

#[cfg(test)]
mod tests {
    use super::{parse_oid, validate_batch};

    #[test]
    fn remote_identity_accepts_normalized_and_provider_forms() {
        assert_eq!(parse_oid("hyperliquid:42").unwrap(), 42);
        assert_eq!(parse_oid("42").unwrap(), 42);
        assert!(parse_oid("not-an-order").is_err());
    }

    #[test]
    fn batches_are_non_empty_and_bounded() {
        assert!(validate_batch(0).is_err());
        assert!(validate_batch(1).is_ok());
        assert!(validate_batch(1_000).is_ok());
        assert!(validate_batch(1_001).is_err());
    }
}
