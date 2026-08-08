//! Runtime intent planning and safety checks.
//!
//! Execution owns the plan and lifecycle, while this composition adapter
//! talks to the already-running Account/Risk/Market processes through their
//! Unix sockets.  No business state is cached here.

use crate::application::{ExecuteStrategyIntent, ExecutionPreflight, SubmitOrder};
use crate::domain::{ExecutionFill, ExecutionOrder, ExecutionOrderStatus, OrderSide, OrderType};
use serde_json::Value;
use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};

pub struct SocketExecutionPreflight {
    accounts: BTreeMap<String, PathBuf>,
    market: Option<PathBuf>,
    reference: Option<PathBuf>,
    risk: Option<PathBuf>,
    reservations: BTreeMap<String, String>,
    reservation_amounts: BTreeMap<String, i64>,
    reservation_quantities: BTreeMap<String, i64>,
    orders: BTreeMap<String, (String, String)>,
}

const MAX_PRICE_DEVIATION_BPS: i64 = 500;

impl SocketExecutionPreflight {
    pub fn from_manifest(path: impl AsRef<Path>) -> Result<Self, String> {
        let value: Value = serde_json::from_slice(
            &std::fs::read(path).map_err(|error| format!("read endpoint manifest: {error}"))?,
        )
        .map_err(|error| format!("decode endpoint manifest: {error}"))?;
        let mut accounts = BTreeMap::new();
        for (account_id, endpoint) in value
            .get("accounts")
            .and_then(Value::as_object)
            .ok_or_else(|| "endpoint manifest has no accounts".to_string())?
        {
            let socket = endpoint
                .get("socket")
                .and_then(Value::as_str)
                .ok_or_else(|| format!("account {account_id} has no socket"))?;
            accounts.insert(account_id.clone(), PathBuf::from(socket));
        }
        let components = value.get("components").and_then(Value::as_object);
        let endpoint = |name: &str| {
            components
                .and_then(|items| items.get(name))
                .and_then(|item| item.get("socket"))
                .and_then(Value::as_str)
                .map(PathBuf::from)
        };
        Ok(Self {
            accounts,
            market: endpoint("market"),
            reference: endpoint("reference"),
            risk: endpoint("risk"),
            reservations: BTreeMap::new(),
            reservation_amounts: BTreeMap::new(),
            reservation_quantities: BTreeMap::new(),
            orders: BTreeMap::new(),
        })
    }

    fn request(
        socket: &Path,
        method: &str,
        path: &str,
        body: Option<&Value>,
    ) -> Result<Value, String> {
        let mut stream = UnixStream::connect(socket)
            .map_err(|error| format!("connect {}: {error}", socket.display()))?;
        let payload = body.map_or_else(Vec::new, |value| serde_json::to_vec(value).unwrap());
        write!(
            stream,
            "{method} {path} HTTP/1.1\r\nHost: execution\r\nContent-Length: {}\r\n\r\n",
            payload.len()
        )
        .map_err(|error| error.to_string())?;
        stream
            .write_all(&payload)
            .map_err(|error| error.to_string())?;
        let mut response = Vec::new();
        stream
            .read_to_end(&mut response)
            .map_err(|error| error.to_string())?;
        let text = String::from_utf8_lossy(&response);
        let (_, raw) = text
            .split_once("\r\n\r\n")
            .ok_or_else(|| "invalid unix HTTP response".to_string())?;
        serde_json::from_str(raw).map_err(|error| format!("decode {path} response: {error}"))
    }

    fn health(&self, account_id: &str) -> Result<(), String> {
        let socket = self
            .accounts
            .get(account_id)
            .ok_or_else(|| format!("account is not bound: {account_id}"))?;
        let response = Self::request(socket, "GET", "/v1/health", None)?;
        if response.get("status").and_then(Value::as_str) != Some("ready")
            || response.get("lease_valid").and_then(Value::as_bool) == Some(false)
        {
            return Err(format!("account {account_id} is not ready"));
        }
        let capabilities = Self::request(socket, "GET", "/v1/capabilities", None)?;
        let can_trade = capabilities
            .get("capabilities")
            .and_then(Value::as_array)
            .is_some_and(|items| {
                items.iter().any(|item| {
                    item.get("account_id").and_then(Value::as_str) == Some(account_id)
                        && item.get("can_trade").and_then(Value::as_bool) == Some(true)
                })
            });
        if !can_trade {
            return Err(format!(
                "account {account_id} does not have trade authorization"
            ));
        }
        Ok(())
    }
}

impl ExecutionPreflight for SocketExecutionPreflight {
    fn plan_intent(&mut self, intent: &ExecuteStrategyIntent) -> Result<Vec<SubmitOrder>, String> {
        if let Some(market) = &self.market {
            let snapshot = Self::request(market, "GET", "/v1/snapshot", None)?;
            if snapshot.get("observations").is_none() && snapshot.get("order_books").is_none() {
                return Err("market snapshot has no observations or order books".into());
            }
            if let Some(limit) = intent.limit_price_mantissa {
                validate_market_price(
                    &snapshot,
                    intent,
                    limit,
                    intent.limit_price_scale.unwrap_or(intent.quantity_scale),
                )?;
            }
        }
        let mut orders = Vec::with_capacity(intent.account_ids.len());
        for (index, account_id) in intent.account_ids.iter().enumerate() {
            self.health(account_id)?;
            let positions = Self::request(
                self.accounts.get(account_id).unwrap(),
                "GET",
                &format!("/v1/positions?symbol={}", intent.instrument_id),
                None,
            )?;
            let current = find_position(&positions, &intent.instrument_id)
                .unwrap_or((0, intent.quantity_scale));
            let target = scale_decimal(
                intent.target_quantity_mantissa,
                intent.quantity_scale,
                current.1,
            )?;
            let delta = target
                .checked_sub(current.0)
                .ok_or_else(|| "intent quantity overflow".to_string())?;
            if delta == 0 {
                continue;
            }
            orders.push(SubmitOrder {
                order_id: format!("{}:order:{}", intent.intent_id, index),
                intent_id: Some(intent.intent_id.clone()),
                account_id: account_id.clone(),
                segment_key: intent.segment_key.clone(),
                instrument_id: intent.instrument_id.clone(),
                market_id: intent.market_id.clone(),
                side: if delta > 0 {
                    OrderSide::Buy
                } else {
                    OrderSide::Sell
                },
                order_type: if intent.limit_price_mantissa.is_some() {
                    OrderType::Limit
                } else {
                    OrderType::Market
                },
                quantity_mantissa: delta.unsigned_abs() as i64,
                quantity_scale: current.1,
                limit_price_mantissa: intent.limit_price_mantissa,
                limit_price_scale: intent.limit_price_scale,
                options: Default::default(),
            });
        }
        Ok(orders)
    }

    fn validate_order(&mut self, request: &SubmitOrder) -> Result<(), String> {
        self.health(&request.account_id)?;
        if request.quantity_mantissa <= 0 {
            return Err("order quantity must be positive".into());
        }
        if request.order_type == OrderType::Limit
            && request.limit_price_mantissa.unwrap_or_default() <= 0
        {
            return Err("limit price must be positive".into());
        }
        if let Some(reference) = &self.reference {
            let query = request
                .market_id
                .as_ref()
                .map(|market_id| format!("market_id={market_id}"))
                .unwrap_or_else(|| format!("symbol={}&active_only=true", request.instrument_id));
            let market = Self::request(
                reference,
                "GET",
                &format!("/v1/markets/resolve?{query}"),
                None,
            )?;
            validate_reference_rules(&market, request)?;
        }
        let balances = Self::request(
            self.accounts.get(&request.account_id).unwrap(),
            "GET",
            "/v1/balances",
            None,
        )?;
        let asset = match request.side {
            OrderSide::Buy => request.options.quote_asset.clone().or_else(|| {
                request
                    .instrument_id
                    .strip_suffix("USDT")
                    .map(|_| "USDT".into())
            }),
            OrderSide::Sell => request
                .instrument_id
                .strip_suffix("USDT")
                .map(|value| value.to_string()),
        };
        if let Some(asset) = asset {
            let needed = if request.side == OrderSide::Buy {
                request
                    .quantity_mantissa
                    .checked_mul(request.limit_price_mantissa.unwrap_or(0))
                    .ok_or_else(|| "order notional overflow".to_string())?
            } else {
                request.quantity_mantissa
            };
            let available = find_available(&balances, &asset)
                .ok_or_else(|| format!("no available balance for {asset}"))?;
            if available < needed {
                return Err(format!("insufficient available balance for {asset}"));
            }
        }
        Ok(())
    }
    fn reserve_order(&mut self, request: &SubmitOrder) -> Result<(), String> {
        let risk = self
            .risk
            .as_ref()
            .ok_or_else(|| "risk endpoint is not configured".to_string())?;
        let reservation_id = format!("execution:{}", request.order_id);
        let amount = request
            .quantity_mantissa
            .checked_mul(request.limit_price_mantissa.unwrap_or(1))
            .ok_or_else(|| "risk notional overflow".to_string())?;
        let body = serde_json::json!({
            "reservation_id": reservation_id,
            "assessment": {
                "request_id": request.order_id,
                "usages": [{"metric":"notional","amount":{"mantissa":amount,"scale":request.quantity_scale},"budgets":[]}],
                "at_unix_nanos": now_unix_nanos()
            }
        });
        Self::request(risk, "POST", "/v1/reserve", Some(&body))?;
        self.reservations.insert(
            request.order_id.clone(),
            format!("execution:{}", request.order_id),
        );
        self.reservation_amounts
            .insert(request.order_id.clone(), amount);
        self.reservation_quantities
            .insert(request.order_id.clone(), request.quantity_mantissa);
        Ok(())
    }
    fn prepare_order(&mut self, request: &SubmitOrder) -> Result<(), String> {
        let account = self
            .accounts
            .get(&request.account_id)
            .ok_or_else(|| format!("account is not bound: {}", request.account_id))?;
        let body = serde_json::json!({
            "order_id": request.order_id,
            "intent_id": request.intent_id,
            "account_id": request.account_id,
            "segment_key": request.segment_key,
            "instrument_id": request.instrument_id,
            "market_id": request.market_id,
            "side": if request.side == OrderSide::Buy { "Buy" } else { "Sell" },
            "quantity": {"mantissa": request.quantity_mantissa, "scale": request.quantity_scale},
            "order_type": if request.order_type == OrderType::Limit { "Limit" } else { "Market" },
            "limit_price": request.limit_price_mantissa.map(|mantissa| serde_json::json!({"mantissa": mantissa, "scale": request.limit_price_scale.unwrap_or(request.quantity_scale)})),
        });
        Self::request(account, "POST", "/v1/plan-order", Some(&body))?;
        self.orders.insert(
            request.order_id.clone(),
            (request.account_id.clone(), request.segment_key.clone()),
        );
        Ok(())
    }
    fn publish_order(&mut self, order: &ExecutionOrder) -> Result<(), String> {
        let account_id = &order.account_id;
        let socket = self
            .accounts
            .get(account_id)
            .ok_or_else(|| format!("account is not bound: {account_id}"))?;
        let body = serde_json::json!({
            "order_id": order.order_id,
            "status": account_order_status(order.status),
            "venue_order_id": order.venue_order_id,
            "filled_quantity": {"mantissa": order.filled_quantity_mantissa, "scale": order.filled_quantity_scale},
            "occurred_at_unix_nanos": order.updated_at_unix_nanos,
            "reason": order.reason,
        });
        Self::request(socket, "POST", "/v1/order-event", Some(&body)).map(|_| ())
    }
    fn publish_fill(&mut self, fill: &ExecutionFill) -> Result<(), String> {
        let (account_id, segment_key) = self
            .orders
            .get(&fill.order_id)
            .cloned()
            .ok_or_else(|| format!("order fact is not prepared: {}", fill.order_id))?;
        let socket = self
            .accounts
            .get(&account_id)
            .ok_or_else(|| format!("account is not bound: {account_id}"))?;
        let body = serde_json::json!({
            "fill_id": fill.fill_id,
            "order_id": fill.order_id,
            "segment_key": segment_key,
            "instrument_id": fill.instrument_id,
            "quantity": {"mantissa": fill.quantity_mantissa, "scale": fill.quantity_scale},
            "price": {"mantissa": fill.price_mantissa, "scale": fill.price_scale},
            "side": if fill.side == OrderSide::Buy { "Buy" } else { "Sell" },
            "occurred_at_unix_nanos": fill.occurred_at_unix_nanos,
        });
        Self::request(socket, "POST", "/v1/fill", Some(&body)).map(|_| ())
    }
    fn resize_order(
        &mut self,
        order_id: &str,
        remaining_quantity_mantissa: i64,
        quantity_scale: u8,
    ) -> Result<(), String> {
        let risk = self
            .risk
            .as_ref()
            .ok_or_else(|| "risk endpoint is not configured".to_string())?;
        let old_id = self
            .reservations
            .get(order_id)
            .cloned()
            .ok_or_else(|| "order reservation is missing".to_string())?;
        let old_body = serde_json::json!({"reservation_id": old_id});
        Self::request(risk, "POST", "/v1/release", Some(&old_body))?;
        let previous = self
            .reservation_amounts
            .get(order_id)
            .copied()
            .unwrap_or_default();
        let original_quantity = self
            .reservation_quantities
            .get(order_id)
            .copied()
            .unwrap_or(remaining_quantity_mantissa)
            .max(1);
        let amount = (remaining_quantity_mantissa as i128)
            .checked_mul(previous.max(1) as i128)
            .and_then(|value| value.checked_div(original_quantity as i128))
            .and_then(|value| i64::try_from(value).ok())
            .unwrap_or(original_quantity);
        let new_id = format!("execution:{order_id}:remaining:{remaining_quantity_mantissa}");
        let body = serde_json::json!({
            "reservation_id": new_id,
            "assessment": {
                "request_id": new_id,
                "usages": [{"metric":"notional","amount":{"mantissa":amount,"scale":quantity_scale},"budgets":[]}],
                "at_unix_nanos": now_unix_nanos()
            }
        });
        Self::request(risk, "POST", "/v1/reserve", Some(&body))?;
        self.reservations.insert(order_id.to_owned(), new_id);
        self.reservation_amounts.insert(order_id.to_owned(), amount);
        self.reservation_quantities
            .insert(order_id.to_owned(), remaining_quantity_mantissa);
        Ok(())
    }
    fn release_order(&mut self, order_id: &str) -> Result<(), String> {
        if let (Some(risk), Some(reservation_id)) = (&self.risk, self.reservations.get(order_id)) {
            let body = serde_json::json!({"reservation_id": reservation_id});
            Self::request(risk, "POST", "/v1/release", Some(&body))?;
        }
        self.reservations.remove(order_id);
        self.reservation_amounts.remove(order_id);
        self.reservation_quantities.remove(order_id);
        Ok(())
    }
    fn consume_order(&mut self, order_id: &str) -> Result<(), String> {
        if let (Some(risk), Some(reservation_id)) = (&self.risk, self.reservations.get(order_id)) {
            let body = serde_json::json!({"reservation_id": reservation_id});
            Self::request(risk, "POST", "/v1/consume", Some(&body))?;
        }
        self.reservations.remove(order_id);
        self.reservation_amounts.remove(order_id);
        self.reservation_quantities.remove(order_id);
        Ok(())
    }
}

fn validate_reference_rules(market: &Value, request: &SubmitOrder) -> Result<(), String> {
    let status = market
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if !matches!(
        status.to_ascii_lowercase().as_str(),
        "active" | "listed" | "trading"
    ) {
        return Err("instrument or market is not tradable".into());
    }
    let quantity = request.quantity_mantissa as f64 / 10_f64.powi(request.quantity_scale as i32);
    if let Some(minimum) = market
        .get("minimum_quantity")
        .and_then(Value::as_str)
        .and_then(parse_float)
    {
        if quantity < minimum {
            return Err("order quantity is below the market minimum".into());
        }
    }
    if let Some(tick) = market
        .get("quantity_tick")
        .and_then(Value::as_str)
        .and_then(parse_float)
    {
        if !is_multiple(quantity, tick) {
            return Err("order quantity violates lot size".into());
        }
    }
    if let Some(price_mantissa) = request.limit_price_mantissa {
        let price = price_mantissa as f64
            / 10_f64.powi(request.limit_price_scale.unwrap_or(request.quantity_scale) as i32);
        if let Some(tick) = market
            .get("price_tick")
            .and_then(Value::as_str)
            .and_then(parse_float)
        {
            if !is_multiple(price, tick) {
                return Err("order price violates tick size".into());
            }
        }
        if let Some(minimum) = market
            .get("minimum_notional")
            .and_then(Value::as_str)
            .and_then(parse_float)
        {
            if price * quantity < minimum {
                return Err("order notional is below the market minimum".into());
            }
        }
    }
    Ok(())
}

fn account_order_status(status: ExecutionOrderStatus) -> &'static str {
    match status {
        ExecutionOrderStatus::Pending => "Planned",
        ExecutionOrderStatus::Submitting => "Submitting",
        ExecutionOrderStatus::Accepted => "Acknowledged",
        ExecutionOrderStatus::PartiallyFilled => "PartiallyFilled",
        ExecutionOrderStatus::Filled => "Filled",
        ExecutionOrderStatus::CancelRequested => "CancelRequested",
        ExecutionOrderStatus::Canceled => "Canceled",
        ExecutionOrderStatus::Rejected => "Rejected",
        ExecutionOrderStatus::Expired => "Expired",
        ExecutionOrderStatus::Unknown => "Unknown",
        ExecutionOrderStatus::Failed => "Unknown",
    }
}

fn parse_float(value: &str) -> Option<f64> {
    value.parse().ok().filter(|value: &f64| *value > 0.0)
}

fn is_multiple(value: f64, step: f64) -> bool {
    let quotient = value / step;
    (quotient - quotient.round()).abs() < 1e-8
}

fn find_available(value: &Value, asset: &str) -> Option<i64> {
    if let Some(object) = value.as_object() {
        if object
            .get("asset_code")
            .and_then(Value::as_str)
            .is_some_and(|code| code.eq_ignore_ascii_case(asset))
        {
            let available = object.get("available")?;
            return Some(decimal_mantissa(available)?);
        }
        for child in object.values() {
            if let Some(found) = find_available(child, asset) {
                return Some(found);
            }
        }
    }
    if let Some(array) = value.as_array() {
        for child in array {
            if let Some(found) = find_available(child, asset) {
                return Some(found);
            }
        }
    }
    None
}

fn decimal_mantissa(value: &Value) -> Option<i64> {
    value.get("mantissa").and_then(Value::as_i64)
}

fn find_position(value: &Value, instrument: &str) -> Option<(i64, u8)> {
    if let Some(object) = value.as_object() {
        if object
            .get("instrument_id")
            .and_then(Value::as_str)
            .is_some_and(|id| id.eq_ignore_ascii_case(instrument))
        {
            let quantity = object.get("quantity")?;
            return Some((
                decimal_mantissa(quantity)?,
                quantity.get("scale")?.as_u64()? as u8,
            ));
        }
        for child in object.values() {
            if let Some(found) = find_position(child, instrument) {
                return Some(found);
            }
        }
    }
    if let Some(array) = value.as_array() {
        for child in array {
            if let Some(found) = find_position(child, instrument) {
                return Some(found);
            }
        }
    }
    None
}

fn scale_decimal(mantissa: i64, from: u8, to: u8) -> Result<i64, String> {
    if from == to {
        return Ok(mantissa);
    }
    if from < to {
        mantissa
            .checked_mul(
                10_i64
                    .checked_pow((to - from) as u32)
                    .ok_or_else(|| "quantity scale overflow".to_string())?,
            )
            .ok_or_else(|| "quantity scale overflow".to_string())
    } else {
        Ok(mantissa / 10_i64.pow((from - to) as u32))
    }
}

fn validate_market_price(
    snapshot: &Value,
    intent: &ExecuteStrategyIntent,
    limit_mantissa: i64,
    limit_scale: u8,
) -> Result<(), String> {
    let Some(latest) = snapshot.get("latest") else {
        return Err("market snapshot has no latest quote".into());
    };
    let quote = find_quote(latest, &intent.instrument_id)
        .ok_or_else(|| "market quote is unavailable".to_string())?;
    let reference = quote
        .1
        .or(quote.0)
        .ok_or_else(|| "market quote has no executable side".to_string())?;
    let limit = limit_mantissa as f64 / 10_f64.powi(limit_scale as i32);
    let reference = reference
        .parse::<f64>()
        .map_err(|_| "market quote price is invalid".to_string())?;
    if reference <= 0.0
        || ((limit - reference).abs() / reference) * 10_000.0 > MAX_PRICE_DEVIATION_BPS as f64
    {
        return Err("limit price deviates too far from the current market quote".into());
    }
    Ok(())
}

fn find_quote(value: &Value, instrument: &str) -> Option<(Option<String>, Option<String>)> {
    if let Some(object) = value.as_object() {
        if object
            .get("instrument_id")
            .and_then(Value::as_str)
            .is_some_and(|id| id.eq_ignore_ascii_case(instrument))
        {
            return Some((
                object
                    .get("bid_price")
                    .and_then(Value::as_str)
                    .map(str::to_owned),
                object
                    .get("ask_price")
                    .and_then(Value::as_str)
                    .map(str::to_owned),
            ));
        }
        for child in object.values() {
            if let Some(found) = find_quote(child, instrument) {
                return Some(found);
            }
        }
    }
    if let Some(array) = value.as_array() {
        for child in array {
            if let Some(found) = find_quote(child, instrument) {
                return Some(found);
            }
        }
    }
    None
}

fn now_unix_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}
