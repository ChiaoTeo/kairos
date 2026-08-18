//! OKX-native ordinary-order extensions that do not widen the shared capability surface.

use kairos_primitives::{ClientOrderId, ParticipantSymbol, Price, Quantity, RemoteOrderId};
use serde_json::Value;

use crate::{CommandOutcome, IndeterminateCommand, IntegrationError, ParticipantRejection};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OkxOrderIdentity {
    pub instrument: ParticipantSymbol,
    pub remote_order_id: Option<RemoteOrderId>,
    pub client_order_id: Option<ClientOrderId>,
}

impl OkxOrderIdentity {
    pub(crate) fn validate(&self) -> Result<(), IntegrationError> {
        if self.remote_order_id.is_none() && self.client_order_id.is_none() {
            return Err(IntegrationError::InvalidRequest(
                "OKX order identity requires remote_order_id or client_order_id".into(),
            ));
        }
        Ok(())
    }

    pub(crate) fn body(&self) -> Result<Value, IntegrationError> {
        self.validate()?;
        let mut body = serde_json::Map::from_iter([(
            "instId".into(),
            Value::String(self.instrument.to_string()),
        )]);
        if let Some(order_id) = &self.remote_order_id {
            body.insert("ordId".into(), Value::String(order_id.to_string()));
        } else if let Some(client_order_id) = &self.client_order_id {
            body.insert("clOrdId".into(), Value::String(client_order_id.to_string()));
        }
        Ok(Value::Object(body))
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OkxAmendOrderRequest {
    pub identity: OkxOrderIdentity,
    pub request_id: Option<String>,
    pub new_quantity: Option<Quantity>,
    pub new_price: Option<Price>,
    pub cancel_on_fail: bool,
}

impl OkxAmendOrderRequest {
    pub(crate) fn body(&self) -> Result<Value, IntegrationError> {
        if self.new_quantity.is_none() && self.new_price.is_none() {
            return Err(IntegrationError::InvalidRequest(
                "OKX amend requires new_quantity or new_price".into(),
            ));
        }
        if self
            .request_id
            .as_ref()
            .is_some_and(|value| value.is_empty() || value.len() > 32 || !value.is_ascii())
        {
            return Err(IntegrationError::InvalidRequest(
                "OKX amend request_id must be 1-32 ASCII characters".into(),
            ));
        }
        let Value::Object(mut body) = self.identity.body()? else {
            unreachable!("order identity body is an object")
        };
        body.insert("cxlOnFail".into(), Value::Bool(self.cancel_on_fail));
        if let Some(value) = &self.request_id {
            body.insert("reqId".into(), Value::String(value.clone()));
        }
        if let Some(value) = self.new_quantity {
            body.insert("newSz".into(), Value::String(value.to_string()));
        }
        if let Some(value) = self.new_price {
            body.insert("newPx".into(), Value::String(value.to_string()));
        }
        Ok(Value::Object(body))
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OkxOrderOperationAck {
    pub remote_order_id: Option<RemoteOrderId>,
    pub client_order_id: Option<ClientOrderId>,
    pub request_id: Option<String>,
    pub message: String,
}

pub(crate) fn one_ack(
    payload: &Value,
    operation: &str,
) -> Result<CommandOutcome<OkxOrderOperationAck>, IntegrationError> {
    let Some(row) = payload
        .get("data")
        .and_then(Value::as_array)
        .and_then(|rows| rows.first())
    else {
        return Ok(CommandOutcome::Indeterminate(
            IndeterminateCommand::may_have_been_sent(format!(
                "OKX {operation} response data is missing"
            )),
        ));
    };
    row_ack(row, operation)
}

pub(crate) fn batch_acks(
    payload: &Value,
    expected: usize,
    operation: &str,
) -> Result<Vec<CommandOutcome<OkxOrderOperationAck>>, IntegrationError> {
    let rows = payload.get("data").and_then(Value::as_array);
    Ok((0..expected)
        .map(|index| match rows.and_then(|rows| rows.get(index)) {
            Some(row) => row_ack(row, operation).unwrap_or_else(|error| {
                CommandOutcome::Indeterminate(IndeterminateCommand::may_have_been_sent(
                    error.to_string(),
                ))
            }),
            None => CommandOutcome::Indeterminate(IndeterminateCommand::may_have_been_sent(
                format!("OKX {operation} response item {index} is missing"),
            )),
        })
        .collect())
}

fn row_ack(
    row: &Value,
    operation: &str,
) -> Result<CommandOutcome<OkxOrderOperationAck>, IntegrationError> {
    let code = row.get("sCode").and_then(Value::as_str).unwrap_or("0");
    let message = row
        .get("sMsg")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    if code != "0" {
        return Ok(CommandOutcome::Rejected(ParticipantRejection {
            code: Some(code.into()),
            message: if message.is_empty() {
                format!("OKX rejected {operation}")
            } else {
                message
            },
            participant_request_id: row
                .get("reqId")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty())
                .map(str::to_owned),
        }));
    }
    Ok(CommandOutcome::Confirmed(OkxOrderOperationAck {
        remote_order_id: optional_id(row, "ordId", |value| RemoteOrderId::new(value.to_owned()))?,
        client_order_id: optional_id(row, "clOrdId", |value| ClientOrderId::new(value.to_owned()))?,
        request_id: row
            .get("reqId")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .map(str::to_owned),
        message,
    }))
}

fn optional_id<T>(
    row: &Value,
    field: &str,
    parse: impl FnOnce(&str) -> Result<T, kairos_primitives::DomainTypeError>,
) -> Result<Option<T>, IntegrationError> {
    row.get(field)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(parse)
        .transpose()
        .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))
}

pub(crate) fn validate_batch(length: usize, operation: &str) -> Result<(), IntegrationError> {
    if !(1..=20).contains(&length) {
        return Err(IntegrationError::InvalidRequest(format!(
            "OKX {operation} batch must contain 1-20 items"
        )));
    }
    Ok(())
}

pub(crate) fn cancel_body(identity: &OkxOrderIdentity) -> Result<Value, IntegrationError> {
    identity.body()
}

pub(crate) fn amend_body(request: &OkxAmendOrderRequest) -> Result<Value, IntegrationError> {
    request.body()
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    fn identity() -> OkxOrderIdentity {
        OkxOrderIdentity {
            instrument: ParticipantSymbol::new("BTC-USDT").unwrap(),
            remote_order_id: Some(RemoteOrderId::new("590909145319051111").unwrap()),
            client_order_id: None,
        }
    }

    #[test]
    fn amend_body_is_typed_and_requires_a_change() {
        let request = OkxAmendOrderRequest {
            identity: identity(),
            request_id: Some("amend-1".into()),
            new_quantity: Some("2".parse().unwrap()),
            new_price: None,
            cancel_on_fail: false,
        };
        assert_eq!(request.body().unwrap()["newSz"], json!("2"));

        let invalid = OkxAmendOrderRequest {
            new_quantity: None,
            ..request
        };
        assert!(invalid.body().is_err());
    }

    #[test]
    fn batch_results_preserve_partial_success() {
        let outcomes = batch_acks(
            &json!({"data":[
                {"ordId":"1","sCode":"0","sMsg":""},
                {"ordId":"2","sCode":"51000","sMsg":"bad size"}
            ]}),
            2,
            "batch amend",
        )
        .unwrap();
        assert!(matches!(outcomes[0], CommandOutcome::Confirmed(_)));
        assert!(matches!(outcomes[1], CommandOutcome::Rejected(_)));
    }

    #[test]
    fn missing_batch_item_is_indeterminate_not_rejected() {
        let outcomes = batch_acks(&json!({"data":[]}), 1, "batch cancel").unwrap();
        assert!(matches!(outcomes[0], CommandOutcome::Indeterminate(_)));
    }
}
