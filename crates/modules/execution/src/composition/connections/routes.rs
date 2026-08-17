//! Multi-route capability assembly over provider-native Integration handles.

use super::super::providers::*;
use super::*;
use crate::application::ExecutionAsyncRoute;
use crate::services::routing::{ExecutionRoute, RoutedAsyncOrderEntry, RoutedAsyncOrderQuery};
use kairos_integration::application::ParticipantInstrumentTypeRef;
use kairos_integration::participants::binance::ConnectionDomain as BinanceConnectionDomain;
use kairos_integration::participants::binance::{
    BinanceFuturesChannelConfig, BinanceMarginChannelConfig, BinanceOptionsChannelConfig,
    BinanceSpotConnection,
};
use kairos_integration::participants::okx::{OkxConnection, OkxPrivateChannelConfig};

pub fn compose_execution_routes(
    options: &[ExecutionConnectionOptions],
) -> Result<ExecutionConnections, String> {
    let Some(_) = options.first() else {
        return Err("at least one Execution route is required".into());
    };
    if options.len() == 1 {
        let provider = options[0].participant_id.trim().to_ascii_lowercase();
        let product = options[0].product.trim().to_ascii_lowercase();
        if matches!(provider.as_str(), "simulated" | "paper") {
            return compose_execution_connections(&options[0]);
        }
        if provider == "ibkr" {
            return compose_ibkr_async_execution(&options[0]);
        }
        let native_async = (provider == "binance"
            && matches!(
                product.as_str(),
                "spot"
                    | "cross-margin"
                    | "isolated-margin"
                    | "usd-m-futures"
                    | "coin-m-futures"
                    | "options"
            ))
            || provider == "okx"
            || provider == "okex";
        if !native_async {
            return Err(format!(
                "production async execution route is not available for {} {}; migrate the provider-native async capability",
                options[0].participant_id, options[0].product
            ));
        }
    }
    let mut ibkr_client_identities = std::collections::BTreeSet::new();
    for option in options {
        let provider = option.participant_id.trim().to_ascii_lowercase();
        let product = option.product.trim().to_ascii_lowercase();
        match provider.as_str() {
            "binance"
                if matches!(
                    product.as_str(),
                    "spot"
                        | "cross-margin"
                        | "isolated-margin"
                        | "usd-m-futures"
                        | "coin-m-futures"
                        | "options"
                ) => {}
            "ibkr" if matches!(product.as_str(), "equity" | "stocks") => {
                let identity = (
                    option.host.trim().to_ascii_lowercase(),
                    option.port,
                    option.client_id,
                );
                if !ibkr_client_identities.insert(identity) {
                    return Err(format!(
                        "duplicate IBKR host/port/client_id across Execution routes: {}:{} client_id={}; allocate a distinct TWS client id per order-event route",
                        option.host, option.port, option.client_id
                    ));
                }
            }
            "okx" | "okex" => {
                okx_trading_shape(&product, option.trading_mode.as_deref())?;
            }
            _ => {
                return Err(format!(
                    "multi-route async composition is not yet available for {} {}; migrate the provider-native capability first",
                    option.participant_id, option.product
                ))
            }
        }
    }

    let mut binance_contexts: Vec<(usize, BinanceSpotConnection)> = Vec::new();
    let mut okx_contexts: Vec<(usize, OkxConnection)> = Vec::new();
    let mut descriptors = Vec::with_capacity(options.len());
    let mut async_entry_routes = Vec::with_capacity(options.len());
    let mut async_query_routes = Vec::with_capacity(options.len());
    let mut streams = Vec::with_capacity(options.len());

    for (option_index, option) in options.iter().enumerate() {
        let account_id = kairos_primitives::AccountId::new(option.account_id.clone())
            .map_err(|error| error.to_string())?;
        let segment_key = kairos_primitives::SegmentKey::new(option.segment_key.clone())
            .map_err(|error| error.to_string())?;
        let provider = option.participant_id.trim().to_ascii_lowercase();
        let product = option.product.trim().to_ascii_lowercase();
        if provider == "binance" {
            let connection = if matches!(
                product.as_str(),
                "spot" | "cross-margin" | "isolated-margin"
            ) {
                let context_index = if let Some(index) =
                    binance_contexts.iter().position(|(representative, _)| {
                        same_binance_spot_provider_context(&options[*representative], option)
                    }) {
                    index
                } else {
                    binance_contexts
                        .push((option_index, binance_spot_provider_connection(option)?));
                    binance_contexts.len() - 1
                };
                Some(binance_spot_private_connection_from_provider(
                    &binance_contexts[context_index].1,
                    option,
                )?)
            } else {
                None
            };
            if matches!(product.as_str(), "cross-margin" | "isolated-margin") {
                let (margin, provider_instrument_type, isolated_symbol): (
                    _,
                    ParticipantInstrumentTypeRef,
                    Option<String>,
                ) = if product == "isolated-margin" {
                    let symbol = option
                        .isolated_symbol
                        .as_ref()
                        .filter(|value| !value.trim().is_empty())
                        .ok_or_else(|| {
                            "Binance isolated-margin route requires isolated_symbol".to_string()
                        })?
                        .to_ascii_uppercase();
                    (
                        connection
                            .as_ref()
                            .expect("Spot family connection for margin route")
                            .isolated_margin_connection(symbol.clone())
                            .map_err(|error| error.to_string())?,
                        BinanceConnectionDomain::IsolatedMargin.into(),
                        Some(symbol),
                    )
                } else {
                    (
                        connection
                            .as_ref()
                            .expect("Spot family connection for margin route")
                            .cross_margin_connection(),
                        BinanceConnectionDomain::CrossMargin.into(),
                        None,
                    )
                };
                let descriptor = margin.descriptor().clone();
                async_entry_routes.push(ExecutionRoute::new(
                    option.route_id.clone(),
                    account_id.clone(),
                    segment_key.clone(),
                    Some(provider_instrument_type.clone()),
                    descriptor.clone(),
                    ExecutionAsyncOrderEntry::BinanceMargin(
                        margin.order_entry().map_err(|error| error.to_string())?,
                    ),
                )?);
                async_query_routes.push(ExecutionRoute::new(
                    option.route_id.clone(),
                    account_id,
                    segment_key,
                    Some(provider_instrument_type),
                    descriptor.clone(),
                    ExecutionAsyncOrderQuery::BinanceMargin(
                        margin.order_query().map_err(|error| error.to_string())?,
                    ),
                )?);
                streams.push(
                    ExecutionAsyncRoute::new(
                        option.route_id.clone(),
                        option.required,
                        ExecutionAsyncEventSource::BinanceMargin(
                            margin
                                .order_events(&BinanceMarginChannelConfig {
                                    websocket_stream_url: option.websocket_url.clone(),
                                    isolated_symbol,
                                    event_queue_capacity: option.order_event_queue_capacity,
                                })
                                .map_err(|error| error.to_string())?,
                        ),
                    )
                    .with_binding_id(descriptor.binding_id.clone()),
                );
                descriptors.push(descriptor);
                continue;
            }
            if matches!(product.as_str(), "usd-m-futures" | "coin-m-futures") {
                let (futures, provider_instrument_type): (_, ParticipantInstrumentTypeRef) =
                    if product == "usd-m-futures" {
                        (
                            binance_futures_private_connection(
                                option,
                                BinanceConnectionDomain::UsdMFutures,
                            )?,
                            BinanceConnectionDomain::UsdMFutures.into(),
                        )
                    } else {
                        (
                            binance_futures_private_connection(
                                option,
                                BinanceConnectionDomain::CoinMFutures,
                            )?,
                            BinanceConnectionDomain::CoinMFutures.into(),
                        )
                    };
                let descriptor = futures.descriptor().clone();
                async_entry_routes.push(ExecutionRoute::new(
                    option.route_id.clone(),
                    account_id.clone(),
                    segment_key.clone(),
                    Some(provider_instrument_type.clone()),
                    descriptor.clone(),
                    ExecutionAsyncOrderEntry::BinanceFutures(futures.order_entry()),
                )?);
                async_query_routes.push(ExecutionRoute::new(
                    option.route_id.clone(),
                    account_id,
                    segment_key,
                    Some(provider_instrument_type),
                    descriptor.clone(),
                    ExecutionAsyncOrderQuery::BinanceFutures(futures.order_query()),
                )?);
                streams.push(
                    ExecutionAsyncRoute::new(
                        option.route_id.clone(),
                        option.required,
                        ExecutionAsyncEventSource::BinanceFutures(
                            futures
                                .order_events(&BinanceFuturesChannelConfig {
                                    websocket_stream_url: option.websocket_url.clone(),
                                    event_queue_capacity: option.order_event_queue_capacity,
                                })
                                .map_err(|error| error.to_string())?,
                        ),
                    )
                    .with_binding_id(descriptor.binding_id.clone()),
                );
                descriptors.push(descriptor);
                continue;
            }
            if product == "options" {
                let options_connection = binance_options_private_connection(option)?;
                let descriptor = options_connection.descriptor().clone();
                async_entry_routes.push(ExecutionRoute::new(
                    option.route_id.clone(),
                    account_id.clone(),
                    segment_key.clone(),
                    Some(BinanceConnectionDomain::Options.into()),
                    descriptor.clone(),
                    ExecutionAsyncOrderEntry::BinanceOptions(options_connection.order_entry()),
                )?);
                async_query_routes.push(ExecutionRoute::new(
                    option.route_id.clone(),
                    account_id,
                    segment_key,
                    Some(BinanceConnectionDomain::Options.into()),
                    descriptor.clone(),
                    ExecutionAsyncOrderQuery::BinanceOptions(options_connection.order_query()),
                )?);
                streams.push(
                    ExecutionAsyncRoute::new(
                        option.route_id.clone(),
                        option.required,
                        ExecutionAsyncEventSource::BinanceOptions(
                            options_connection
                                .order_events(&BinanceOptionsChannelConfig {
                                    websocket_stream_url: option.websocket_url.clone(),
                                    event_queue_capacity: option.order_event_queue_capacity,
                                })
                                .map_err(|error| error.to_string())?,
                        ),
                    )
                    .with_binding_id(descriptor.binding_id.clone()),
                );
                descriptors.push(descriptor);
                continue;
            }
            let connection = connection.expect("Spot family connection for spot route");
            let descriptor = connection.spot_descriptor();
            let channel = binance_spot_channel_config(option);
            async_entry_routes.push(ExecutionRoute::new(
                option.route_id.clone(),
                account_id.clone(),
                segment_key.clone(),
                Some(BinanceConnectionDomain::Spot.into()),
                descriptor.clone(),
                ExecutionAsyncOrderEntry::BinanceSpot(
                    connection
                        .spot_order_entry()
                        .map_err(|error| error.to_string())?,
                ),
            )?);
            async_query_routes.push(ExecutionRoute::new(
                option.route_id.clone(),
                account_id,
                segment_key,
                Some(BinanceConnectionDomain::Spot.into()),
                descriptor.clone(),
                ExecutionAsyncOrderQuery::BinanceSpot(
                    connection
                        .spot_order_query()
                        .map_err(|error| error.to_string())?,
                ),
            )?);
            streams.push(
                ExecutionAsyncRoute::new(
                    option.route_id.clone(),
                    option.required,
                    ExecutionAsyncEventSource::BinanceSpot(
                        connection
                            .spot_order_events(&channel)
                            .map_err(|error| error.to_string())?,
                    ),
                )
                .with_binding_id(descriptor.binding_id.clone()),
            );
            descriptors.push(descriptor);
            continue;
        }

        if provider == "ibkr" {
            let connection = ibkr_connection(option)?;
            let descriptor = connection.descriptor().clone();
            async_entry_routes.push(ExecutionRoute::new(
                option.route_id.clone(),
                account_id.clone(),
                segment_key.clone(),
                Some(ParticipantInstrumentTypeRef::new("equity")?),
                descriptor.clone(),
                ExecutionAsyncOrderEntry::Ibkr(connection.order_entry()),
            )?);
            async_query_routes.push(ExecutionRoute::new(
                option.route_id.clone(),
                account_id,
                segment_key,
                Some(ParticipantInstrumentTypeRef::new("equity")?),
                descriptor.clone(),
                ExecutionAsyncOrderQuery::Ibkr(connection.order_query()),
            )?);
            streams.push(
                ExecutionAsyncRoute::new(
                    option.route_id.clone(),
                    option.required,
                    ExecutionAsyncEventSource::Ibkr(connection.order_events(None)),
                )
                .with_binding_id(descriptor.binding_id.clone()),
            );
            descriptors.push(descriptor);
            continue;
        }

        let (instrument_type, trading_mode) =
            okx_trading_shape(&product, option.trading_mode.as_deref())?;
        let context_index = if let Some(index) =
            okx_contexts.iter().position(|(representative, _)| {
                same_okx_provider_context(&options[*representative], option)
            }) {
            index
        } else {
            okx_contexts.push((option_index, okx_provider_connection(option)?));
            okx_contexts.len() - 1
        };
        let connection =
            okx_private_connection_from_provider(&okx_contexts[context_index].1, option)?;
        let async_entry = connection
            .trading_order_entry(instrument_type, trading_mode)
            .map_err(|error| error.to_string())?;
        let entry_descriptor = async_entry.descriptor().clone();
        let async_query = connection.trading_order_query(instrument_type);
        let query_descriptor = async_query.descriptor().clone();
        async_entry_routes.push(ExecutionRoute::new(
            option.route_id.clone(),
            account_id.clone(),
            segment_key.clone(),
            Some(instrument_type.into()),
            entry_descriptor.clone(),
            ExecutionAsyncOrderEntry::OkxTrading(async_entry),
        )?);
        async_query_routes.push(ExecutionRoute::new(
            option.route_id.clone(),
            account_id,
            segment_key,
            Some(instrument_type.into()),
            query_descriptor,
            ExecutionAsyncOrderQuery::OkxTrading(async_query),
        )?);
        streams.push(
            ExecutionAsyncRoute::new(
                option.route_id.clone(),
                option.required,
                ExecutionAsyncEventSource::OkxTrading(
                    connection
                        .trading_order_events(
                            instrument_type,
                            trading_mode,
                            &OkxPrivateChannelConfig {
                                websocket_url: option.websocket_url.clone(),
                                event_queue_capacity: option.order_event_queue_capacity,
                            },
                        )
                        .map_err(|error| error.to_string())?,
                ),
            )
            .with_binding_id(entry_descriptor.binding_id.clone()),
        );
        descriptors.push(entry_descriptor);
    }

    Ok(ExecutionConnections {
        descriptor: descriptors.first().cloned(),
        descriptors,
        order_entry: None,
        order_query: None,
        execution_stream: None,
        async_order_entry: Some(ExecutionAsyncOrderEntryRoutes {
            inner: RoutedAsyncOrderEntry::new(async_entry_routes)?,
            writer_fences: Vec::new(),
        }),
        async_order_query: Some(ExecutionAsyncOrderQueryRoutes {
            inner: RoutedAsyncOrderQuery::new(async_query_routes)?,
        }),
        async_execution_streams: streams,
    })
}

/// Compose one-shot/direct CLI adapters. Migrated providers use their native
/// async capability and bounded Execution proxies; remaining explicit CLI
/// Direct one-shot adapters are retained only for explicitly synchronous
/// callers; the production route is composed exclusively from async clients.
pub fn compose_execution_connections(
    options: &ExecutionConnectionOptions,
) -> Result<ExecutionConnections, String> {
    let provider = options.participant_id.trim().to_ascii_lowercase();
    let product = options.product.trim().to_ascii_lowercase();
    if provider == "binance" && product == "spot" {
        let connection = binance_spot_private_connection(options)?;
        let descriptor = connection.spot_descriptor();
        let channel = binance_spot_channel_config(options);
        let async_order_entry = ExecutionAsyncOrderEntry::BinanceSpot(
            connection
                .spot_order_entry()
                .map_err(|error| error.to_string())?,
        );
        let async_order_query = ExecutionAsyncOrderQuery::BinanceSpot(
            connection
                .spot_order_query()
                .map_err(|error| error.to_string())?,
        );
        let account_id = kairos_primitives::AccountId::new(options.account_id.clone())
            .map_err(|error| error.to_string())?;
        let segment_key = kairos_primitives::SegmentKey::new(options.segment_key.clone())
            .map_err(|error| error.to_string())?;
        let entry_routes = ExecutionAsyncOrderEntryRoutes {
            inner: RoutedAsyncOrderEntry::new(vec![ExecutionRoute::new(
                options.route_id.clone(),
                account_id.clone(),
                segment_key.clone(),
                Some(BinanceConnectionDomain::Spot.into()),
                descriptor.clone(),
                async_order_entry,
            )?])?,
            writer_fences: Vec::new(),
        };
        let query_routes = ExecutionAsyncOrderQueryRoutes {
            inner: RoutedAsyncOrderQuery::new(vec![ExecutionRoute::new(
                options.route_id.clone(),
                account_id,
                segment_key,
                Some(BinanceConnectionDomain::Spot.into()),
                descriptor.clone(),
                async_order_query,
            )?])?,
        };
        let binding_id = descriptor.binding_id.clone();
        return Ok(ExecutionConnections {
            descriptor: Some(descriptor.clone()),
            descriptors: vec![descriptor],
            order_entry: None,
            order_query: None,
            execution_stream: None,
            async_order_entry: Some(entry_routes),
            async_order_query: Some(query_routes),
            async_execution_streams: vec![ExecutionAsyncRoute::new(
                options.route_id.clone(),
                options.required,
                ExecutionAsyncEventSource::BinanceSpot(
                    connection
                        .spot_order_events(&channel)
                        .map_err(|error| error.to_string())?,
                ),
            )
            .with_binding_id(binding_id)],
        });
    }
    if provider == "okx" || provider == "okex" {
        let (instrument_type, trading_mode) =
            okx_trading_shape(&product, options.trading_mode.as_deref())?;
        let connection = okx_private_connection(options)?;
        let async_entry = connection
            .trading_order_entry(instrument_type, trading_mode)
            .map_err(|error| error.to_string())?;
        let entry_descriptor = async_entry.descriptor().clone();
        let async_query = connection.trading_order_query(instrument_type);
        let query_descriptor = async_query.descriptor().clone();
        let order_events = connection
            .trading_order_events(
                instrument_type,
                trading_mode,
                &OkxPrivateChannelConfig {
                    websocket_url: options.websocket_url.clone(),
                    event_queue_capacity: options.order_event_queue_capacity,
                },
            )
            .map_err(|error| error.to_string())?;
        let account_id = kairos_primitives::AccountId::new(options.account_id.clone())
            .map_err(|error| error.to_string())?;
        let segment_key = kairos_primitives::SegmentKey::new(options.segment_key.clone())
            .map_err(|error| error.to_string())?;
        let entry_routes = ExecutionAsyncOrderEntryRoutes {
            inner: RoutedAsyncOrderEntry::new(vec![ExecutionRoute::new(
                options.route_id.clone(),
                account_id.clone(),
                segment_key.clone(),
                Some(instrument_type.into()),
                entry_descriptor.clone(),
                ExecutionAsyncOrderEntry::OkxTrading(async_entry),
            )?])?,
            writer_fences: Vec::new(),
        };
        let query_routes = ExecutionAsyncOrderQueryRoutes {
            inner: RoutedAsyncOrderQuery::new(vec![ExecutionRoute::new(
                options.route_id.clone(),
                account_id,
                segment_key,
                Some(instrument_type.into()),
                query_descriptor,
                ExecutionAsyncOrderQuery::OkxTrading(async_query),
            )?])?,
        };
        let binding_id = entry_descriptor.binding_id.clone();
        return Ok(ExecutionConnections {
            descriptor: Some(entry_descriptor.clone()),
            descriptors: vec![entry_descriptor],
            order_entry: None,
            order_query: None,
            execution_stream: None,
            async_order_entry: Some(entry_routes),
            async_order_query: Some(query_routes),
            async_execution_streams: vec![ExecutionAsyncRoute::new(
                options.route_id.clone(),
                options.required,
                ExecutionAsyncEventSource::OkxTrading(order_events),
            )
            .with_binding_id(binding_id)],
        });
    }

    Ok(ExecutionConnections {
        descriptor: None,
        descriptors: Vec::new(),
        order_entry: Some(compose_order_entry(options)?),
        order_query: compose_order_query(options)?,
        execution_stream: compose_execution_stream(options)?,
        async_order_entry: None,
        async_order_query: None,
        async_execution_streams: Vec::new(),
    })
}
