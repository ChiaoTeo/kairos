//! Multi-route assembly over provider-native concrete Integration connections.

use super::*;
use crate::services::gateway::ExecutionConnectionPlan;

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
        let installed = concrete_route(system, option)?;
        plans.push(ExecutionConnectionPlan {
            route_id: option.route_id.clone(),
            required: option.required,
            account_id: kairos_primitives::AccountId::new(&option.account_id)
                .map_err(|e| e.to_string())?,
            segment_key: kairos_primitives::SegmentKey::new(&option.segment_key)
                .map_err(|e| e.to_string())?,
            instrument_type,
            entry_key: installed.entry_descriptor.connection_key.to_string(),
            query_key: installed.query_descriptor.connection_key.to_string(),
            stream_key: installed.stream_descriptor.connection_key.to_string(),
        });
        descriptors.push(installed.entry_descriptor);
    }
    Ok((plans, descriptors))
}

struct InstalledRoute {
    entry_descriptor: ConnectionDescriptor,
    query_descriptor: ConnectionDescriptor,
    stream_descriptor: ConnectionDescriptor,
}

fn concrete_route(
    system: &mut kairos_conflux::ConfluxSystem,
    option: &ExecutionConnectionOptions,
) -> Result<InstalledRoute, String> {
    let provider = option.participant_id.trim().to_ascii_lowercase();
    let product = normalize(&option.product);
    let binding_id = format!("execution.{}", option.route_id);
    match provider.as_str() {
        "binance" => binance_route(system, option, &product, binding_id),
        "okx" | "okex" => okx_route(system, option, &product, binding_id),
        "ibkr" => ibkr_route(system, option, &product, binding_id),
        _ => Err(format!(
            "production execution route is not available for {} {}",
            option.participant_id, option.product
        )),
    }
}

fn binance_route(
    system: &mut kairos_conflux::ConfluxSystem,
    option: &ExecutionConnectionOptions,
    product: &str,
    binding_id: String,
) -> Result<InstalledRoute, String> {
    let key = |suffix: &str| {
        kairos_conflux::ConnectionKey::new(format!("{binding_id}.{suffix}"))
            .map_err(|error| error.to_string())
    };
    let rest = || kairos_conflux::BinanceRestConfig {
        environment: environment(option),
        endpoint: option.base_url.clone(),
        credential: Some(binance_credential(option)),
    };
    let user = || kairos_conflux::BinanceUserWebSocketConfig {
        environment: environment(option),
        rest_endpoint: option.base_url.clone(),
        websocket_endpoint: option.websocket_url.clone(),
        credential: binance_credential(option),
        event_capacity: option.order_event_queue_capacity.max(1),
        segment_key: option.segment_key.clone(),
    };

    macro_rules! family {
        ($rest:ident, $stream:ident, $name:literal) => {{
            let entry_key = key(concat!($name, ".command"))?;
            let query_key = key(concat!($name, ".query"))?;
            let stream_key = key(concat!($name, ".stream"))?;
            system
                .connections()
                .$rest
                .create(entry_key.clone(), rest())
                .map_err(|error| error.to_string())?;
            let entry_descriptor = system
                .connections()
                .$rest
                .get(&entry_key)
                .map_err(|error| error.to_string())?
                .descriptor()
                .clone();
            system
                .connections()
                .$rest
                .create(query_key.clone(), rest())
                .map_err(|error| error.to_string())?;
            let query_descriptor = system
                .connections()
                .$rest
                .get(&query_key)
                .map_err(|error| error.to_string())?
                .descriptor()
                .clone();
            system
                .connections()
                .$stream
                .create_with_options(stream_key.clone(), user(), connection_options(option))
                .map_err(|error| error.to_string())?;
            let stream_descriptor = system
                .connections()
                .$stream
                .get(&stream_key)
                .map_err(|error| error.to_string())?
                .descriptor()
                .clone();
            Ok(InstalledRoute {
                entry_descriptor,
                query_descriptor,
                stream_descriptor,
            })
        }};
    }

    match product {
        "spot" => family!(binance_spot_rest, binance_spot_user_websocket, "spot"),
        "cross-margin" | "isolated-margin" => {
            if product == "isolated-margin"
                && option
                    .isolated_symbol
                    .as_deref()
                    .is_none_or(|value| value.trim().is_empty())
            {
                return Err("Binance isolated-margin route requires isolated_symbol".into());
            }
            family!(binance_margin_rest, binance_margin_user_websocket, "margin")
        }
        "usd-m-futures" => family!(binance_usdm_rest, binance_usdm_user_websocket, "usdm"),
        "coin-m-futures" => family!(binance_coinm_rest, binance_coinm_user_websocket, "coinm"),
        "options" => family!(
            binance_options_rest,
            binance_options_user_websocket,
            "options"
        ),
        "equity" | "stocks" => {
            family!(binance_stocks_rest, binance_stocks_user_websocket, "stocks")
        }
        _ => Err(format!("unsupported Binance execution product: {product}")),
    }
}

fn okx_route(
    system: &mut kairos_conflux::ConfluxSystem,
    option: &ExecutionConnectionOptions,
    product: &str,
    binding_id: String,
) -> Result<InstalledRoute, String> {
    let trading_mode = okx_trading_mode(product, option.trading_mode.as_deref())?;
    let credential = okx_credential(option);
    let rest_config = |_suffix: &str| kairos_conflux::OkxPrivateRestConfig {
        connection: kairos_conflux::OkxRestConfig {
            environment: environment(option),
            endpoint: option.base_url.clone(),
        },
        credential: credential.clone(),
    };
    let entry_key = kairos_conflux::ConnectionKey::new(format!("{binding_id}.command"))?;
    let query_key = kairos_conflux::ConnectionKey::new(format!("{binding_id}.query"))?;
    let stream_key = kairos_conflux::ConnectionKey::new(format!("{binding_id}.stream"))?;
    system
        .connections()
        .okx_private_rest
        .create(entry_key.clone(), rest_config("command"))
        .map_err(|error| error.to_string())?;
    let entry_descriptor = system
        .connections()
        .okx_private_rest
        .get(&entry_key)
        .map_err(|error| error.to_string())?
        .descriptor()
        .clone();
    system
        .connections()
        .okx_private_rest
        .create(query_key.clone(), rest_config("query"))
        .map_err(|error| error.to_string())?;
    let query_descriptor = system
        .connections()
        .okx_private_rest
        .get(&query_key)
        .map_err(|error| error.to_string())?
        .descriptor()
        .clone();
    system
        .connections()
        .okx_private_websocket
        .create_with_options(
            stream_key.clone(),
            kairos_conflux::OkxPrivateWebSocketConfig {
                connection: kairos_conflux::OkxWebSocketConfig {
                    environment: environment(option),
                    endpoint: option.websocket_url.clone(),
                    event_capacity: option.order_event_queue_capacity.max(1),
                },
                rest_endpoint: option.base_url.clone(),
                credential,
                segment_key: option.segment_key.clone(),
                trading_mode,
            },
            connection_options(option),
        )
        .map_err(|error| error.to_string())?;
    let stream_descriptor = system
        .connections()
        .okx_private_websocket
        .get(&stream_key)
        .map_err(|error| error.to_string())?
        .descriptor()
        .clone();
    Ok(InstalledRoute {
        entry_descriptor,
        query_descriptor,
        stream_descriptor,
    })
}

fn ibkr_route(
    system: &mut kairos_conflux::ConfluxSystem,
    option: &ExecutionConnectionOptions,
    product: &str,
    binding_id: String,
) -> Result<InstalledRoute, String> {
    if !matches!(product, "equity" | "stocks" | "spot") {
        return Err(format!("unsupported IBKR execution product: {product}"));
    }
    let order_config = || kairos_conflux::IbkrOrderConfig {
        environment: environment(option),
        host: option.host.clone(),
        port: option.port,
        client_id: option.client_id,
        account_id: option.account_id.clone(),
    };
    let entry_key = kairos_conflux::ConnectionKey::new(format!("{binding_id}.command"))?;
    let query_key = kairos_conflux::ConnectionKey::new(format!("{binding_id}.query"))?;
    let stream_key = kairos_conflux::ConnectionKey::new(format!("{binding_id}.stream"))?;
    system
        .connections()
        .ibkr_order
        .create_with_options(
            entry_key.clone(),
            order_config(),
            connection_options(option),
        )
        .map_err(|error| error.to_string())?;
    let entry_descriptor = system
        .connections()
        .ibkr_order
        .get(&entry_key)
        .map_err(|error| error.to_string())?
        .descriptor()
        .clone();
    system
        .connections()
        .ibkr_order
        .create_with_options(
            query_key.clone(),
            order_config(),
            connection_options(option),
        )
        .map_err(|error| error.to_string())?;
    let query_descriptor = system
        .connections()
        .ibkr_order
        .get(&query_key)
        .map_err(|error| error.to_string())?
        .descriptor()
        .clone();
    system
        .connections()
        .ibkr_execution_stream
        .create_with_options(
            stream_key.clone(),
            kairos_conflux::IbkrExecutionStreamConfig {
                environment: environment(option),
                host: option.host.clone(),
                port: option.port,
                client_id: option.client_id.saturating_add(1),
                account_id: option.account_id.clone(),
                symbol: None,
            },
            connection_options(option),
        )
        .map_err(|error| error.to_string())?;
    let stream_descriptor = system
        .connections()
        .ibkr_execution_stream
        .get(&stream_key)
        .map_err(|error| error.to_string())?
        .descriptor()
        .clone();
    Ok(InstalledRoute {
        entry_descriptor,
        query_descriptor,
        stream_descriptor,
    })
}

fn binance_credential(option: &ExecutionConnectionOptions) -> kairos_conflux::BinanceCredential {
    kairos_conflux::BinanceCredential {
        principal_id: option.principal_scope_id.clone(),
        api_key: option.api_key.clone(),
        secret: option.secret.clone(),
    }
}

fn connection_options(
    option: &ExecutionConnectionOptions,
) -> kairos_conflux::ConnectionCreateOptions {
    kairos_conflux::ConnectionCreateOptions {
        required: option.required,
        recovery: kairos_conflux::RecoveryPolicy::default(),
    }
}

fn okx_credential(option: &ExecutionConnectionOptions) -> kairos_conflux::OkxCredential {
    kairos_conflux::OkxCredential {
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
