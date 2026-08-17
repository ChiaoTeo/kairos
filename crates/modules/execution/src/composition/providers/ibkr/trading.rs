//! IBKR-native trading capability construction.

use super::super::super::connections::{
    ExecutionAsyncEventSource, ExecutionAsyncOrderEntry, ExecutionAsyncOrderEntryRoutes,
    ExecutionAsyncOrderQuery, ExecutionAsyncOrderQueryRoutes, ExecutionConnectionOptions,
    ExecutionConnections,
};
use crate::application::ExecutionAsyncRoute;
use crate::services::routing::{ExecutionRoute, RoutedAsyncOrderEntry, RoutedAsyncOrderQuery};
use kairos_integration::application::ParticipantInstrumentTypeRef;
use kairos_integration::participants::ibkr;

pub(in crate::composition) fn connection(
    options: &ExecutionConnectionOptions,
) -> Result<ibkr::IbkrConnection, String> {
    ibkr::IbkrConnection::connect(
        ibkr::IbkrConnectionConfig {
            host: options.host.clone(),
            port: options.port,
            client_id: options.client_id,
        },
        format!("ibkr.principal.{}", options.principal_scope_id),
        options.account_id.clone(),
    )
    .map_err(|error| error.to_string())
}

pub(in crate::composition) fn compose_async_execution(
    options: &ExecutionConnectionOptions,
) -> Result<ExecutionConnections, String> {
    match options.product.trim().to_ascii_lowercase().as_str() {
        "equity" | "stocks" => {}
        product => return Err(format!("unsupported IBKR execution product: {product}")),
    }
    let connection = connection(options)?;
    let descriptor = connection.descriptor().clone();
    let account_id = kairos_primitives::AccountId::new(options.account_id.clone())
        .map_err(|error| error.to_string())?;
    let segment_key = kairos_primitives::SegmentKey::new(options.segment_key.clone())
        .map_err(|error| error.to_string())?;
    let entry_routes = ExecutionAsyncOrderEntryRoutes {
        inner: RoutedAsyncOrderEntry::new(vec![ExecutionRoute::new(
            options.route_id.clone(),
            account_id.clone(),
            segment_key.clone(),
            Some(ParticipantInstrumentTypeRef::new("equity")?),
            descriptor.clone(),
            ExecutionAsyncOrderEntry::Ibkr(connection.order_entry()),
        )?])?,
        writer_fences: Vec::new(),
    };
    let query_routes = ExecutionAsyncOrderQueryRoutes {
        inner: RoutedAsyncOrderQuery::new(vec![ExecutionRoute::new(
            options.route_id.clone(),
            account_id,
            segment_key,
            Some(ParticipantInstrumentTypeRef::new("equity")?),
            descriptor.clone(),
            ExecutionAsyncOrderQuery::Ibkr(connection.order_query()),
        )?])?,
    };
    let binding_id = descriptor.binding_id.clone();
    Ok(ExecutionConnections {
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
            ExecutionAsyncEventSource::Ibkr(connection.order_events(None)),
        )
        .with_binding_id(binding_id)],
    })
}
