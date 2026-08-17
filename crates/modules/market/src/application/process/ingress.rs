//! Typed Market command ingress and application orchestration.

use std::collections::BTreeMap;
use std::time::Instant;

use kairos_workspace::runtime::{HEALTH_PATH, STOP_PATH};
use serde_json::{json, Value};
use tracing::info;

use super::actor_task::{log_event, CachedCommandResult, MarketActorTask};
use super::lifecycle::now_unix_nanos;
use crate::application::{resolve_market, resolve_option_markets, MarketApplication};
use crate::services::control::wire::{
    command_id, idempotency_key, parse_release_owner_command, parse_subscribe_command,
    parse_unsubscribe_command, strategy_subscription_owner, CommandEnvelope, ReleaseOwnerPayload,
    SubscribePayload, UnsubscribePayload,
};
use crate::services::control::{EngineCommand, MarketHttpResponse};
use crate::SubscriptionId;

const MAX_COMMAND_RESULTS: usize = 4_096;

struct SubscribeRequest {
    request_id: String,
    strategy_id: String,
    launch_id: Option<String>,
    instance_id: String,
    subject: String,
    selectors: Vec<crate::ObservationSelector>,
    exchange: Option<String>,
    market_type: Option<String>,
    asset_type: Option<String>,
    params: BTreeMap<String, Value>,
    dynamic: bool,
}

impl MarketActorTask {
    pub(super) async fn handle_engine_command(
        &mut self,
        command: EngineCommand,
    ) -> Result<(), String> {
        match command {
            EngineCommand::Http(request) => {
                let body = String::from_utf8_lossy(&request.body);
                let response = self
                    .handle_request(&request.method, &request.path, &body)
                    .await;
                let response = self
                    .reconcile_control_sources(&request.path, &body, response)
                    .await;
                self.refresh_command_result(&body, &response);
                let _ = request.response.send(response);
                Ok(())
            }
            EngineCommand::ReconcileMarketUniverse(update) => {
                self.handle_universe_update(update).await
            }
        }
    }

    pub(super) async fn handle_request(
        &mut self,
        method: &str,
        path: &str,
        body: &str,
    ) -> MarketHttpResponse {
        let started = Instant::now();
        log_event(
            "info",
            "market control request",
            json!({"method": method, "path": path}),
        );
        if method == "GET" && path != HEALTH_PATH {
            return MarketHttpResponse {
                status: 405,
                payload: json!({"error":"Market business queries are available only through typed mmap views"}),
            };
        }
        if path == HEALTH_PATH && method != "GET" {
            return MarketHttpResponse {
                status: 405,
                payload: json!({"error":"/v1/health only accepts GET"}),
            };
        }
        let command_key = if matches!(
            path,
            "/v1/subscribe"
                | "/v1/subscriptions"
                | "/v1/unsubscribe"
                | "/v1/subscriptions/release-owner"
        ) {
            idempotency_key(body)
        } else {
            None
        };
        if let Some(key) = command_key.as_deref() {
            if let Some(cached) = self.command_results.get(key).cloned() {
                if cached.request_body == body {
                    return MarketHttpResponse {
                        status: cached.status,
                        payload: cached.payload,
                    };
                }
                return MarketHttpResponse {
                    status: 409,
                    payload: json!({
                        "error": {
                            "code": "command.idempotency_conflict",
                            "message": "idempotency key was already used with a different request",
                            "retryable": false
                        }
                    }),
                };
            }
        }
        let (status, payload) = match path {
            HEALTH_PATH => (200, self.health()),
            "/v1/subscribe" => self.subscribe(body),
            "/v1/subscriptions" => {
                let (status, payload) = self.subscribe(body);
                if status == 202 {
                    let subscription_id = payload
                        .get("subscription_id")
                        .and_then(Value::as_str)
                        .unwrap_or_default();
                    let owner_id = self
                        .application
                        .current_view()
                        .subscriptions
                        .into_iter()
                        .find(|item| item.id.0 == subscription_id)
                        .map(|item| item.owner_id)
                        .unwrap_or_default();
                    (
                        201,
                        json!({
                            "subscription_id": subscription_id,
                            "owner_id": owner_id,
                            "status": payload.get("subscription_status").cloned().unwrap_or(json!("pending")),
                            "members": [],
                            "updated_at_unix_nanos": now_unix_nanos(),
                        }),
                    )
                } else {
                    (status, payload)
                }
            }
            path if path.starts_with("/v1/subscriptions/") && method == "DELETE" => {
                let (status, payload) = self.unsubscribe(body);
                if status == 202 {
                    (204, Value::Null)
                } else {
                    (status, payload)
                }
            }
            "/v1/unsubscribe" => self.unsubscribe(body),
            "/v1/subscriptions/release-owner" => self.release_owner(body),
            "/v1/recover" | "/v1/recovery" => match self.application.recover_sources().await {
                Ok(()) => (
                    202,
                    json!({
                        "command_id": command_id(body).map(Value::String).unwrap_or(Value::Null),
                        "status": "accepted",
                        "operation": "market.recovery"
                    }),
                ),
                Err(error) => (422, json!({"error": error.to_string()})),
            },
            "/v1/replay/pause" => match self.application.set_replay_paused(true).await {
                Ok(()) => (202, json!({"status":"paused"})),
                Err(error) => (422, json!({"error": error.to_string()})),
            },
            "/v1/replay/resume" => match self.application.set_replay_paused(false).await {
                Ok(()) => (202, json!({"status":"running"})),
                Err(error) => (422, json!({"error": error.to_string()})),
            },
            STOP_PATH => {
                self.stop_requested = true;
                (202, json!({"status":"stopping"}))
            }
            _ => (404, json!({"error":"unknown market control path"})),
        };
        if let Some(key) = command_key {
            self.command_results.insert(
                key,
                CachedCommandResult {
                    request_body: body.to_owned(),
                    status,
                    payload: payload.clone(),
                },
            );
            while self.command_results.len() > MAX_COMMAND_RESULTS {
                let Some(oldest_key) = self.command_results.keys().next().cloned() else {
                    break;
                };
                self.command_results.remove(&oldest_key);
            }
        }
        info!(event = "control_response", component = "market", method = %method, path = %path, status, duration_ms = started.elapsed().as_millis(), "market control response sent");
        MarketHttpResponse { status, payload }
    }
    pub(super) fn refresh_command_result(&mut self, body: &str, response: &MarketHttpResponse) {
        let Some(key) = idempotency_key(body) else {
            return;
        };
        self.command_results.insert(
            key,
            CachedCommandResult {
                request_body: body.to_owned(),
                status: response.status,
                payload: response.payload.clone(),
            },
        );
        while self.command_results.len() > MAX_COMMAND_RESULTS {
            let Some(oldest_key) = self.command_results.keys().next().cloned() else {
                break;
            };
            self.command_results.remove(&oldest_key);
        }
    }
    pub(super) fn refresh_subscription_status(&self, response: &mut MarketHttpResponse) {
        let Some(object) = response.payload.as_object_mut() else {
            return;
        };
        let Some(subscription_id) = object
            .get("subscription_id")
            .and_then(Value::as_str)
            .and_then(|value| SubscriptionId::new(value).ok())
        else {
            return;
        };
        if let Some(status) = self.application.subscription_status(&subscription_id) {
            object.insert("subscription_status".into(), json!(status));
        }
    }
    pub(super) fn health(&self) -> Value {
        let snapshot = self.application.current_view();
        json!({
            "pid": std::process::id(),
            "status": match snapshot.feed_status {
                // A server without a configured feed is still available for
                // control-plane validation and subscription commands.
                crate::FeedStatus::Disconnected => "ready",
                crate::FeedStatus::Ready => "ready",
                crate::FeedStatus::Reconnecting => "reconnecting",
                crate::FeedStatus::WarmingUp => "warming_up",
                crate::FeedStatus::Degraded => "degraded",
            },
            "dependencies": { "feed_status": snapshot.feed_status },
        })
    }
    pub(super) fn subscribe(&mut self, body: &str) -> (u16, Value) {
        let value: CommandEnvelope<SubscribePayload> = match parse_subscribe_command(body) {
            Ok(value) => value,
            Err(error) => {
                return (
                    400,
                    json!({"error":{"code":"command.invalid_json","message":format!("invalid subscribe command: {error}"),"retryable":false}}),
                )
            }
        };
        if !matches!(value.schema_version, 1 | 2)
            || value.operation != "market.subscribe"
            || value.command_id.trim().is_empty()
            || value.idempotency_key.trim().is_empty()
        {
            return (
                422,
                json!({"error":{"code":"command.invalid_envelope","message":"unsupported market command schema or operation","retryable":false}}),
            );
        }
        let selectors = match value
            .payload
            .selectors
            .iter()
            .map(|value| crate::ObservationSelector::parse(value))
            .collect::<Result<Vec<_>, _>>()
        {
            Ok(selectors) => selectors,
            Err(error) => return (422, json!({"error": error})),
        };
        let request = SubscribeRequest {
            request_id: value.command_id,
            strategy_id: value.strategy_id,
            launch_id: value.launch_id,
            instance_id: value.instance_id,
            subject: value.payload.subject,
            selectors,
            exchange: value.payload.exchange,
            market_type: value.payload.market_type,
            asset_type: value.payload.asset_type,
            params: value.payload.params,
            dynamic: value.payload.dynamic,
        };
        if request.request_id.trim().is_empty()
            || request.strategy_id.trim().is_empty()
            || request.instance_id.trim().is_empty()
            || request.subject.trim().is_empty()
        {
            return (
                422,
                json!({"error":"request_id, strategy_id, instance_id and subject are required"}),
            );
        }
        let chain_mode = request
            .params
            .get("mode")
            .and_then(Value::as_str)
            .is_some_and(|mode| mode.eq_ignore_ascii_case("chain"));
        let source_symbol = request
            .subject
            .strip_prefix("market.")
            .unwrap_or(&request.subject)
            .to_owned();
        let exchange = request.exchange.clone().unwrap_or_else(|| "binance".into());
        let market_type = request.market_type.clone().unwrap_or_else(|| "spot".into());
        let subscription_id = match SubscriptionId::new(request.request_id.clone()) {
            Ok(value) => value,
            Err(error) => return (422, json!({"error": error})),
        };
        if chain_mode {
            let underlying = match request.params.get("underlying").and_then(Value::as_str) {
                Some(value) if !value.trim().is_empty() => value.trim().to_owned(),
                _ => {
                    return (
                        422,
                        json!({"error":"chain subscription requires params.underlying"}),
                    )
                }
            };
            let market_universe = self.application.market_universe();
            if market_universe.is_empty() {
                return (422, json!({"error":"market universe is not ready"}));
            }
            if market_type != "options" {
                return (
                    422,
                    json!({"error":"chain subscription requires market_type=options"}),
                );
            }
            let markets = match resolve_option_markets(
                &market_universe,
                &exchange,
                request.asset_type.as_deref(),
                &underlying,
            ) {
                Ok(value) if !value.is_empty() => value,
                Ok(_) => {
                    return (
                        422,
                        json!({"error":format!("market universe has no active option markets for {underlying}")}),
                    )
                }
                Err(error) => return (422, json!({"error": error})),
            };
            let exchange_id = match kairos_primitives::Exchange::new(exchange.clone()) {
                Ok(value) => value,
                Err(error) => return (422, json!({"error": error.to_string()})),
            };
            let asset_type = match request
                .asset_type
                .as_deref()
                .map(str::parse::<kairos_primitives::AssetClass>)
                .transpose()
            {
                Ok(value) => value,
                Err(error) => return (422, json!({"error": error.to_string()})),
            };
            let query = crate::MarketSelectionQuery {
                exchange_id: Some(exchange_id),
                provider_product: Some(
                    kairos_primitives::ProviderProductCode::new(market_type)
                        .expect("validated Reference market type"),
                ),
                asset_type,
                underlying_instrument_id: markets[0].underlying_instrument_id.clone(),
                active_only: true,
                ..Default::default()
            };
            let owner_id = strategy_subscription_owner(
                request.launch_id.as_deref(),
                &request.instance_id,
                &request.strategy_id,
            );
            let result = self.application.subscribe_dynamic_with_selectors(
                subscription_id.clone(),
                owner_id,
                query,
                markets,
                request.selectors.clone(),
            );
            let command_id = request.request_id.clone();
            return match result {
                Err(error) => (
                    422,
                    json!({"status":"rejected","error":{"code":"market.subscription_rejected","message":error.to_string()}}),
                ),
                Ok(diff) => {
                    log_event(
                        "info",
                        "market dynamic subscription accepted",
                        json!({
                            "request_id": request.request_id,
                            "strategy_id": request.strategy_id,
                            "underlying": underlying,
                            "added": diff.added.len(),
                            "removed": diff.removed.len(),
                        }),
                    );
                    (
                        202,
                        json!({
                            "schema_version": 1,
                            "command_id": command_id,
                            "request_id": request.request_id,
                            "instance_id": request.instance_id,
                            "status": "accepted",
                            "subscription_status": self.application.subscription_status(&subscription_id),
                            "mode": "chain",
                            "subscription_id": subscription_id.0,
                            "added": diff.added,
                            "removed": diff.removed,
                            "changed": diff.changed,
                            "rejected": diff.rejected,
                        }),
                    )
                }
            };
        }
        if request.dynamic {
            return (
                422,
                json!({"error":"dynamic subscriptions require params.mode=chain"}),
            );
        }
        let market_universe = self.application.market_universe();
        let descriptor_result = match market_universe.as_slice() {
            [_first, ..] => resolve_market(
                &market_universe,
                &exchange,
                &market_type,
                request.asset_type.as_deref(),
                &source_symbol,
            ),
            _ if self.application.has_replay_source() => {
                let market_id = request
                    .params
                    .get("market_id")
                    .and_then(Value::as_str)
                    .ok_or_else(|| "replay subscription requires params.market_id".to_string());
                let instrument_id = request
                    .params
                    .get("instrument_id")
                    .and_then(Value::as_str)
                    .ok_or_else(|| "replay subscription requires params.instrument_id".to_string());
                market_id.and_then(|market_id| {
                    instrument_id.and_then(|instrument_id| {
                        let instrument_kind = match market_type.as_str() {
                            "equity" => kairos_primitives::InstrumentKind::Equity,
                            "spot" => kairos_primitives::InstrumentKind::Spot,
                            "perpetual" | "swap" => kairos_primitives::InstrumentKind::Perpetual,
                            "future" | "futures" => kairos_primitives::InstrumentKind::Future,
                            "option" | "options" => kairos_primitives::InstrumentKind::Option,
                            "index" => kairos_primitives::InstrumentKind::Index,
                            _ => {
                                return Err(format!("unsupported replay market type {market_type}"))
                            }
                        };
                        let route = crate::MarketDataRoute::new(
                            format!("replay:{market_id}"),
                            "replay",
                            market_type.clone(),
                            source_symbol.clone(),
                        )?;
                        let mut descriptor = crate::ResolvedMarket::new(
                            market_id,
                            instrument_id,
                            instrument_kind,
                            exchange.clone(),
                            route,
                        )?;
                        descriptor.asset_type = request
                            .asset_type
                            .as_deref()
                            .map(str::parse::<kairos_primitives::AssetClass>)
                            .transpose()
                            .map_err(|error| error.to_string())?;
                        descriptor.with_source("replay")
                    })
                })
            }
            _ => {
                Err("market universe is not ready; explicit market-data access is required".into())
            }
        };
        let descriptor = match descriptor_result {
            Ok(value) => value,
            Err(error) => return (422, json!({"error": error})),
        };
        let owner_id = strategy_subscription_owner(
            request.launch_id.as_deref(),
            &request.instance_id,
            &request.strategy_id,
        );
        let result = self.application.subscribe_static_with_selectors(
            subscription_id,
            owner_id,
            descriptor,
            request.selectors.clone(),
        );
        let command_id = request.request_id.clone();
        let selectors = request.selectors.clone();
        match result {
            Err(error) => (
                422,
                json!({
                    "schema_version": 1,
                    "command_id": command_id,
                    "request_id": request.request_id,
                    "status": "rejected",
                    "error": {"code": "market.subscription_rejected", "message": error.to_string(), "retryable": false}
                }),
            ),
            Ok(()) => (
                202,
                json!({
                    "schema_version": 1,
                    "command_id": command_id,
                    "request_id": request.request_id,
                    "instance_id": request.instance_id,
                    "status": "accepted",
                    "subscription_status": self.application.subscription_status(&SubscriptionId::new(request.request_id.clone()).expect("validated subscription id")),
                    "result": {
                        "subscription_id": request.request_id.clone(),
                        "selectors": selectors.clone(),
                    },
                    "subscription_id": request.request_id,
                    "selectors": selectors,
                }),
            ),
        }
    }
    pub(super) fn unsubscribe(&mut self, body: &str) -> (u16, Value) {
        let value: CommandEnvelope<UnsubscribePayload> = match parse_unsubscribe_command(body) {
            Ok(value) => value,
            Err(error) => {
                return (
                    400,
                    json!({"error":{"code":"command.invalid_json","message":format!("invalid unsubscribe command: {error}"),"retryable":false}}),
                )
            }
        };
        if !matches!(value.schema_version, 1 | 2)
            || value.operation != "market.unsubscribe"
            || value.command_id.trim().is_empty()
            || value.idempotency_key.trim().is_empty()
        {
            return (
                422,
                json!({"error":{"code":"command.invalid_envelope","message":"unsupported market command schema or operation","retryable":false}}),
            );
        }
        let request_id = value.command_id;
        let owner_id = strategy_subscription_owner(
            value.launch_id.as_deref(),
            &value.instance_id,
            &value.strategy_id,
        );
        let subscription_id = value.payload.subscription_id;
        let id = match SubscriptionId::new(subscription_id) {
            Ok(value) => value,
            Err(error) => return (422, json!({"error": error})),
        };
        match self.application.unsubscribe_owned(&id, &owner_id) {
            Ok(true) => (
                202,
                json!({"schema_version":1, "command_id":request_id, "request_id": request_id, "status":"accepted"}),
            ),
            Ok(false) => (
                404,
                json!({"schema_version":1, "command_id":request_id, "request_id": request_id, "status":"rejected", "error":{"code":"market.subscription_not_found","message":"subscription not found","retryable":false}}),
            ),
            Err(error) => (
                409,
                json!({"schema_version":1, "command_id":request_id, "request_id": request_id, "status":"rejected", "error":{"code":"market.subscription_owner_mismatch","message":error.to_string(),"retryable":false}}),
            ),
        }
    }
    pub(super) fn release_owner(&mut self, body: &str) -> (u16, Value) {
        let value: CommandEnvelope<ReleaseOwnerPayload> = match parse_release_owner_command(body) {
            Ok(value) => value,
            Err(error) => {
                return (
                    400,
                    json!({"error":{"code":"command.invalid_json","message":format!("invalid release-owner command: {error}"),"retryable":false}}),
                )
            }
        };
        if !matches!(value.schema_version, 1 | 2)
            || value.operation != "market.release_owner"
            || value.command_id.trim().is_empty()
            || value.idempotency_key.trim().is_empty()
            || value.strategy_id.trim().is_empty()
            || value.instance_id.trim().is_empty()
        {
            return (
                422,
                json!({"error":{"code":"command.invalid_envelope","message":"unsupported release-owner command schema or identity","retryable":false}}),
            );
        }
        let owner_id = strategy_subscription_owner(
            value.launch_id.as_deref(),
            &value.instance_id,
            &value.strategy_id,
        );
        let removed = self.application.release_subscription_owner(&owner_id);
        (
            200,
            json!({
                "command_id": value.command_id,
                "status": "completed",
                "removed_subscription_ids": removed,
            }),
        )
    }
}

pub(super) fn rollback_subscribe_intent(application: &mut MarketApplication, body: &str) {
    let Some(command_id) = command_id(body) else {
        return;
    };
    if let Ok(subscription_id) = SubscriptionId::new(command_id) {
        application.unsubscribe(&subscription_id);
    }
}
