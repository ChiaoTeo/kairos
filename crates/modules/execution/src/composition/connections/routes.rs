//! Multi-route assembly over provider-native concrete Integration connections.

use super::*;
use crate::services::gateway::ExecutionConnectionPlan;
use kairos_integration::participants::{binance, ibkr, okx};

pub(crate) fn install_execution_connections(
    system: &mut kairos_conflux::ConfluxSystem,
    options: &[ExecutionConnectionOptions],
) -> Result<(Vec<ExecutionConnectionPlan>, Vec<ConnectionDescriptor>), String> {
    validate_ibkr_client_ids(options)?;
    let mut plans = Vec::new();
    let mut descriptors = Vec::new();
    for option in options {
        if matches!(
            option.participant_id.trim().to_ascii_lowercase().as_str(),
            "simulated" | "paper"
        ) {
            continue;
        }
        let instrument_type = instrument_type(option)?;
        let (entry, query, stream, entry_descriptor) = concrete_route(option)?;
        let query_descriptor = query_descriptor(&query);
        let entry_key = entry_descriptor.binding_id.clone();
        let query_key = query_descriptor.binding_id.clone();
        let stream_key = stream_descriptor(&stream).binding_id.clone();
        install_entry(system, entry_key.clone(), entry)?;
        install_query(system, query_key.clone(), query)?;
        install_stream(system, stream_key.clone(), stream)?;
        plans.push(ExecutionConnectionPlan {
            route_id: option.route_id.clone(),
            required: option.required,
            account_id: kairos_primitives::AccountId::new(&option.account_id)
                .map_err(|e| e.to_string())?,
            segment_key: kairos_primitives::SegmentKey::new(&option.segment_key)
                .map_err(|e| e.to_string())?,
            instrument_type,
            entry_key,
            query_key,
            stream_key,
            entry_descriptor: entry_descriptor.clone(),
            query_descriptor,
        });
        descriptors.push(entry_descriptor);
    }
    Ok((plans, descriptors))
}

fn query_descriptor(value: &ExecutionAsyncOrderQuery) -> ConnectionDescriptor {
    match value {
        ExecutionAsyncOrderQuery::BinanceSpot(v) => v.descriptor(),
        ExecutionAsyncOrderQuery::BinanceMargin(v) => v.descriptor(),
        ExecutionAsyncOrderQuery::BinanceUsdM(v) => v.descriptor(),
        ExecutionAsyncOrderQuery::BinanceCoinM(v) => v.descriptor(),
        ExecutionAsyncOrderQuery::BinanceOptions(v) => v.descriptor(),
        ExecutionAsyncOrderQuery::BinanceStocks(v) => v.descriptor(),
        ExecutionAsyncOrderQuery::OkxTrading(v) => v.descriptor(),
        ExecutionAsyncOrderQuery::Ibkr(v) => v.descriptor(),
    }
    .clone()
}

fn stream_descriptor(value: &ExecutionAsyncEventSource) -> &ConnectionDescriptor {
    match value {
        ExecutionAsyncEventSource::BinanceSpot(v) => v.descriptor(),
        ExecutionAsyncEventSource::BinanceMargin(v) => v.descriptor(),
        ExecutionAsyncEventSource::BinanceUsdM(v) => v.descriptor(),
        ExecutionAsyncEventSource::BinanceCoinM(v) => v.descriptor(),
        ExecutionAsyncEventSource::BinanceOptions(v) => v.descriptor(),
        ExecutionAsyncEventSource::BinanceStocks(v) => v.descriptor(),
        ExecutionAsyncEventSource::OkxTrading(v) => v.descriptor(),
        ExecutionAsyncEventSource::Ibkr(v) => v.descriptor(),
    }
}

fn install_entry(
    system: &mut kairos_conflux::ConfluxSystem,
    key: String,
    value: ExecutionAsyncOrderEntry,
) -> Result<(), String> {
    macro_rules! put {
        ($field:ident, $value:expr) => {
            system
                .$field
                .ensure_with(key, 1, || $value)
                .map(|_| ())
                .map_err(|e| e.to_string())
        };
    }
    match value {
        ExecutionAsyncOrderEntry::BinanceSpot(v) => put!(binance_spot_rest_connections, v),
        ExecutionAsyncOrderEntry::BinanceMargin(v) => put!(binance_margin_rest_connections, v),
        ExecutionAsyncOrderEntry::BinanceUsdM(v) => put!(binance_usdm_rest_connections, v),
        ExecutionAsyncOrderEntry::BinanceCoinM(v) => put!(binance_coinm_rest_connections, v),
        ExecutionAsyncOrderEntry::BinanceOptions(v) => put!(binance_options_rest_connections, v),
        ExecutionAsyncOrderEntry::BinanceStocks(v) => put!(binance_stocks_rest_connections, v),
        ExecutionAsyncOrderEntry::OkxTrading(v) => put!(okx_private_rest_connections, v),
        ExecutionAsyncOrderEntry::Ibkr(v) => put!(ibkr_order_connections, v),
    }
}

fn install_query(
    system: &mut kairos_conflux::ConfluxSystem,
    key: String,
    value: ExecutionAsyncOrderQuery,
) -> Result<(), String> {
    macro_rules! put {
        ($field:ident, $value:expr) => {
            system
                .$field
                .ensure_with(key, 1, || $value)
                .map(|_| ())
                .map_err(|e| e.to_string())
        };
    }
    match value {
        ExecutionAsyncOrderQuery::BinanceSpot(v) => put!(binance_spot_rest_connections, v),
        ExecutionAsyncOrderQuery::BinanceMargin(v) => put!(binance_margin_rest_connections, v),
        ExecutionAsyncOrderQuery::BinanceUsdM(v) => put!(binance_usdm_rest_connections, v),
        ExecutionAsyncOrderQuery::BinanceCoinM(v) => put!(binance_coinm_rest_connections, v),
        ExecutionAsyncOrderQuery::BinanceOptions(v) => put!(binance_options_rest_connections, v),
        ExecutionAsyncOrderQuery::BinanceStocks(v) => put!(binance_stocks_rest_connections, v),
        ExecutionAsyncOrderQuery::OkxTrading(v) => put!(okx_private_rest_connections, v),
        ExecutionAsyncOrderQuery::Ibkr(v) => put!(ibkr_order_connections, v),
    }
}

fn install_stream(
    system: &mut kairos_conflux::ConfluxSystem,
    key: String,
    value: ExecutionAsyncEventSource,
) -> Result<(), String> {
    macro_rules! put {
        ($field:ident, $value:expr) => {
            system
                .$field
                .ensure_with(key, 1, || $value)
                .map(|_| ())
                .map_err(|e| e.to_string())
        };
    }
    match value {
        ExecutionAsyncEventSource::BinanceSpot(v) => {
            put!(binance_spot_user_websocket_connections, v)
        }
        ExecutionAsyncEventSource::BinanceMargin(v) => {
            put!(binance_margin_user_websocket_connections, v)
        }
        ExecutionAsyncEventSource::BinanceUsdM(v) => {
            put!(binance_usdm_user_websocket_connections, v)
        }
        ExecutionAsyncEventSource::BinanceCoinM(v) => {
            put!(binance_coinm_user_websocket_connections, v)
        }
        ExecutionAsyncEventSource::BinanceOptions(v) => {
            put!(binance_options_user_websocket_connections, v)
        }
        ExecutionAsyncEventSource::BinanceStocks(v) => {
            put!(binance_stocks_user_websocket_connections, v)
        }
        ExecutionAsyncEventSource::OkxTrading(v) => put!(okx_private_websocket_connections, v),
        ExecutionAsyncEventSource::Ibkr(v) => put!(ibkr_execution_stream_connections, v),
    }
}

fn concrete_route(
    option: &ExecutionConnectionOptions,
) -> Result<
    (
        ExecutionAsyncOrderEntry,
        ExecutionAsyncOrderQuery,
        ExecutionAsyncEventSource,
        ConnectionDescriptor,
    ),
    String,
> {
    let provider = option.participant_id.trim().to_ascii_lowercase();
    let product = normalize(&option.product);
    let binding_id = format!("execution.{}", option.route_id);
    match provider.as_str() {
        "binance" => binance_route(option, &product, binding_id),
        "okx" | "okex" => okx_route(option, &product, binding_id),
        "ibkr" => ibkr_route(option, &product, binding_id),
        _ => Err(format!(
            "production execution route is not available for {} {}",
            option.participant_id, option.product
        )),
    }
}

fn binance_route(
    option: &ExecutionConnectionOptions,
    product: &str,
    binding_id: String,
) -> Result<
    (
        ExecutionAsyncOrderEntry,
        ExecutionAsyncOrderQuery,
        ExecutionAsyncEventSource,
        ConnectionDescriptor,
    ),
    String,
> {
    let rest = |suffix: &str| binance::BinanceRestConfig {
        binding_id: format!("{binding_id}.{suffix}"),
        environment: environment(option),
        endpoint: option.base_url.clone(),
        credential: Some(binance_credential(option)),
    };
    let user = |suffix: &str| binance::BinanceUserWebSocketConfig {
        binding_id: format!("{binding_id}.{suffix}"),
        environment: environment(option),
        rest_endpoint: option.base_url.clone(),
        websocket_endpoint: option.websocket_url.clone(),
        credential: binance_credential(option),
        event_capacity: option.order_event_queue_capacity.max(1),
        segment_key: option.segment_key.clone(),
    };

    macro_rules! family {
        ($rest:path, $stream:path, $entry:ident, $query:ident, $event:ident, $name:literal) => {{
            let entry_connection = <$rest>::new(rest(concat!($name, ".command")))
                .map_err(|error| error.to_string())?;
            let descriptor = entry_connection.descriptor().clone();
            let query_connection =
                <$rest>::new(rest(concat!($name, ".query"))).map_err(|error| error.to_string())?;
            let stream_connection = <$stream>::new(user(concat!($name, ".stream")))
                .map_err(|error| error.to_string())?;
            Ok((
                ExecutionAsyncOrderEntry::$entry(entry_connection),
                ExecutionAsyncOrderQuery::$query(query_connection),
                ExecutionAsyncEventSource::$event(stream_connection),
                descriptor,
            ))
        }};
    }

    match product {
        "spot" => family!(
            binance::spot::BinanceSpotRestConnection,
            binance::spot::BinanceSpotUserWebSocketConnection,
            BinanceSpot,
            BinanceSpot,
            BinanceSpot,
            "spot"
        ),
        "cross-margin" | "isolated-margin" => {
            if product == "isolated-margin"
                && option
                    .isolated_symbol
                    .as_deref()
                    .is_none_or(|value| value.trim().is_empty())
            {
                return Err("Binance isolated-margin route requires isolated_symbol".into());
            }
            family!(
                binance::margin::BinanceMarginRestConnection,
                binance::margin::BinanceMarginUserWebSocketConnection,
                BinanceMargin,
                BinanceMargin,
                BinanceMargin,
                "margin"
            )
        }
        "usd-m-futures" => family!(
            binance::usdm::BinanceUsdMRestConnection,
            binance::usdm::BinanceUsdMUserWebSocketConnection,
            BinanceUsdM,
            BinanceUsdM,
            BinanceUsdM,
            "usdm"
        ),
        "coin-m-futures" => family!(
            binance::coinm::BinanceCoinMRestConnection,
            binance::coinm::BinanceCoinMUserWebSocketConnection,
            BinanceCoinM,
            BinanceCoinM,
            BinanceCoinM,
            "coinm"
        ),
        "options" => family!(
            binance::options::BinanceOptionsRestConnection,
            binance::options::BinanceOptionsUserWebSocketConnection,
            BinanceOptions,
            BinanceOptions,
            BinanceOptions,
            "options"
        ),
        "equity" | "stocks" => family!(
            binance::advanced::stocks::BinanceStocksRestConnection,
            binance::advanced::stocks::BinanceStocksUserWebSocketConnection,
            BinanceStocks,
            BinanceStocks,
            BinanceStocks,
            "stocks"
        ),
        _ => Err(format!("unsupported Binance execution product: {product}")),
    }
}

fn okx_route(
    option: &ExecutionConnectionOptions,
    product: &str,
    binding_id: String,
) -> Result<
    (
        ExecutionAsyncOrderEntry,
        ExecutionAsyncOrderQuery,
        ExecutionAsyncEventSource,
        ConnectionDescriptor,
    ),
    String,
> {
    let trading_mode = okx_trading_mode(product, option.trading_mode.as_deref())?;
    let credential = okx_credential(option);
    let rest_config = |suffix: &str| okx::OkxPrivateRestConfig {
        connection: okx::OkxRestConfig {
            binding_id: format!("{binding_id}.{suffix}"),
            environment: environment(option),
            endpoint: option.base_url.clone(),
        },
        credential: credential.clone(),
    };
    let entry = okx::private::OkxPrivateRestConnection::new(rest_config("command"))
        .map_err(|error| error.to_string())?;
    let descriptor = entry.descriptor().clone();
    let query = okx::private::OkxPrivateRestConnection::new(rest_config("query"))
        .map_err(|error| error.to_string())?;
    let stream = okx::private::OkxPrivateWebSocketConnection::new(okx::OkxPrivateWebSocketConfig {
        connection: okx::OkxWebSocketConfig {
            binding_id: format!("{binding_id}.stream"),
            environment: environment(option),
            endpoint: option.websocket_url.clone(),
            event_capacity: option.order_event_queue_capacity.max(1),
        },
        credential,
        segment_key: option.segment_key.clone(),
        trading_mode,
    })
    .map_err(|error| error.to_string())?;
    Ok((
        ExecutionAsyncOrderEntry::OkxTrading(entry),
        ExecutionAsyncOrderQuery::OkxTrading(query),
        ExecutionAsyncEventSource::OkxTrading(stream),
        descriptor,
    ))
}

fn ibkr_route(
    option: &ExecutionConnectionOptions,
    product: &str,
    binding_id: String,
) -> Result<
    (
        ExecutionAsyncOrderEntry,
        ExecutionAsyncOrderQuery,
        ExecutionAsyncEventSource,
        ConnectionDescriptor,
    ),
    String,
> {
    if !matches!(product, "equity" | "stocks" | "spot") {
        return Err(format!("unsupported IBKR execution product: {product}"));
    }
    let order_config = || ibkr::IbkrOrderConfig {
        binding_id: format!("{binding_id}.order"),
        environment: environment(option),
        host: option.host.clone(),
        port: option.port,
        client_id: option.client_id,
        account_id: option.account_id.clone(),
    };
    let entry =
        ibkr::IbkrOrderConnection::new(order_config()).map_err(|error| error.to_string())?;
    let descriptor = entry.descriptor().clone();
    let query =
        ibkr::IbkrOrderConnection::new(order_config()).map_err(|error| error.to_string())?;
    let stream = ibkr::IbkrExecutionStreamConnection::new(ibkr::IbkrExecutionStreamConfig {
        binding_id: format!("{binding_id}.stream"),
        environment: environment(option),
        host: option.host.clone(),
        port: option.port,
        client_id: option.client_id.saturating_add(1),
        account_id: option.account_id.clone(),
        symbol: None,
    })
    .map_err(|error| error.to_string())?;
    Ok((
        ExecutionAsyncOrderEntry::Ibkr(entry),
        ExecutionAsyncOrderQuery::Ibkr(query),
        ExecutionAsyncEventSource::Ibkr(stream),
        descriptor,
    ))
}

fn binance_credential(option: &ExecutionConnectionOptions) -> binance::BinanceCredential {
    binance::BinanceCredential {
        principal_id: option.principal_scope_id.clone(),
        api_key: option.api_key.clone(),
        secret: option.secret.clone(),
    }
}

fn okx_credential(option: &ExecutionConnectionOptions) -> okx::OkxCredential {
    okx::OkxCredential {
        principal_id: option.principal_scope_id.clone(),
        api_key: option.api_key.clone(),
        secret: option.secret.clone(),
        passphrase: option.passphrase.clone(),
    }
}

fn environment(option: &ExecutionConnectionOptions) -> String {
    let endpoint = option.base_url.to_ascii_lowercase();
    if endpoint.contains("test") || endpoint.contains("demo") {
        "test".into()
    } else {
        "live".into()
    }
}

fn instrument_type(
    option: &ExecutionConnectionOptions,
) -> Result<ParticipantInstrumentTypeRef, String> {
    let provider = option.participant_id.trim().to_ascii_lowercase();
    let product = normalize(&option.product);
    let value = match provider.as_str() {
        "binance" => match product.as_str() {
            "spot" => "binance-spot",
            "cross-margin" => "binance-cross-margin",
            "isolated-margin" => "binance-isolated-margin",
            "usd-m-futures" => "binance-usdm",
            "coin-m-futures" => "binance-coinm",
            "options" => "binance-options",
            "equity" | "stocks" => "binance-stocks",
            _ => return Err(format!("unsupported Binance execution product: {product}")),
        },
        "okx" | "okex" => match product.as_str() {
            "spot" | "margin" | "swap" | "futures" | "option" | "options" => product.as_str(),
            _ => return Err(format!("unsupported OKX execution product: {product}")),
        },
        "ibkr" => "equity",
        _ => return Err(format!("unsupported execution participant: {provider}")),
    };
    ParticipantInstrumentTypeRef::new(value)
}

fn okx_trading_mode(product: &str, configured: Option<&str>) -> Result<String, String> {
    let mode = configured.map(normalize).unwrap_or_else(|| {
        if product == "spot" {
            "cash".into()
        } else {
            String::new()
        }
    });
    if !matches!(mode.as_str(), "cash" | "cross" | "isolated") {
        return Err(format!(
            "OKX {product} execution route requires trading_mode cash, cross, or isolated"
        ));
    }
    if product == "spot" && mode != "cash" {
        return Err("OKX spot execution requires cash trading_mode".into());
    }
    if product == "margin" && mode == "cash" {
        return Err("OKX margin execution requires cross or isolated trading_mode".into());
    }
    Ok(mode)
}

fn validate_ibkr_client_ids(options: &[ExecutionConnectionOptions]) -> Result<(), String> {
    let mut identities = std::collections::BTreeSet::new();
    for option in options
        .iter()
        .filter(|option| option.participant_id.eq_ignore_ascii_case("ibkr"))
    {
        let identity = (
            option.host.trim().to_ascii_lowercase(),
            option.port,
            option.client_id,
        );
        if !identities.insert(identity) {
            return Err(format!(
                "duplicate IBKR host/port/client_id across Execution routes: {}:{} client_id={}",
                option.host, option.port, option.client_id
            ));
        }
    }
    Ok(())
}

fn normalize(value: &str) -> String {
    value.trim().to_ascii_lowercase().replace('_', "-")
}
