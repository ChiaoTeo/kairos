//! Direct CLI adapters over concrete Execution connections.

use super::routes::{compose_execution_connections, compose_execution_routes};
use super::*;

pub fn compose_direct_execution_connections(
    options: &ExecutionConnectionOptions,
) -> Result<DirectExecutionConnections, String> {
    let provider = options.participant_id.trim().to_ascii_lowercase();
    let product = options.product.trim().to_ascii_lowercase();
    let native_async_direct = provider == "ibkr"
        || (provider == "binance"
            && matches!(
                product.as_str(),
                "cross-margin" | "isolated-margin" | "usd-m-futures" | "coin-m-futures" | "options"
            ));
    if !native_async_direct {
        let connections = compose_execution_connections(options)?;
        return Ok(DirectExecutionConnections {
            order_entry: connections.order_entry,
            order_query: connections.order_query,
            execution_stream: connections.execution_stream,
            runtime: DirectExecutionRuntime::none(),
        });
    }
    tokio::runtime::Handle::try_current()
        .map_err(|_| "direct async execution requires a caller-owned Tokio runtime".to_string())?;
    let mut connections = if provider == "ibkr" {
        compose_ibkr_async_execution(options)?
    } else {
        compose_execution_routes(std::slice::from_ref(options))?
    };
    let entry = connections
        .async_order_entry
        .take()
        .ok_or_else(|| "async order-entry capability is missing".to_string())?;
    let query = connections
        .async_order_query
        .take()
        .ok_or_else(|| "async order-query capability is missing".to_string())?;
    let source = connections
        .async_execution_streams
        .pop()
        .ok_or_else(|| "async order-event capability is missing".to_string())?
        .into_source();
    let (entry_proxy, entry_worker) = AsyncQueuedOrderEntry::channel(entry, 16);
    let (query_proxy, query_worker) = AsyncQueuedOrderQuery::channel(query, 16);
    let (event_proxy, event_worker) = AsyncQueuedOrderEventSource::channel(source, 4);
    let (shutdown, shutdown_rx) = tokio::sync::watch::channel(false);
    let tasks = vec![
        tokio::spawn(entry_worker.run(shutdown_rx.clone())),
        tokio::spawn(query_worker.run(shutdown_rx.clone())),
        tokio::spawn(event_worker.run(shutdown_rx)),
    ];
    Ok(DirectExecutionConnections {
        order_entry: Some(Box::new(entry_proxy)),
        order_query: Some(Box::new(query_proxy)),
        execution_stream: Some(Box::new(event_proxy)),
        runtime: DirectExecutionRuntime {
            shutdown: Some(shutdown),
            _tasks: tasks,
        },
    })
}
