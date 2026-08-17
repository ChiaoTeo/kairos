use super::*;

#[derive(Default)]
pub struct SimulatedOrderEntry;

#[derive(Clone, Debug)]
pub struct ExecutionConnectionOptions {
    /// Business route identity. It is never sent to Integration or a provider.
    pub route_id: String,
    /// Required routes gate process readiness. Optional routes may start and
    /// recover independently while the process reports degraded.
    pub required: bool,
    /// Business account and segment served by this route.
    pub account_id: String,
    pub segment_key: String,
    /// Integration participant selected for this route (for example an
    /// exchange such as `binance` or a broker such as `ibkr`).  This is not
    /// an Account-owned broker identity and must not be used as a generic
    /// vendor/provider bucket.
    pub participant_id: String,
    /// Provider venue product. For OKX this remains independent from the
    /// order/account trading mode below.
    pub product: String,
    pub trading_mode: Option<String>,
    pub api_key: SecretString,
    pub secret: SecretString,
    pub passphrase: SecretString,
    pub base_url: String,
    pub websocket_url: String,
    /// Provider symbol required by Binance isolated-margin listen-key scope.
    pub isolated_symbol: Option<String>,
    pub request_weight_per_minute: u32,
    pub cancel_reserve_weight: u32,
    pub order_event_queue_capacity: usize,
    pub shared_quota_ledger_path: Option<PathBuf>,
    pub egress_scope_id: String,
    pub principal_scope_id: String,
    pub orders_per_10_seconds: u32,
    pub orders_per_day: u32,
    pub host: String,
    pub port: u16,
    pub client_id: i32,
}

/// Business-owned capability composition for one configured execution route.
/// Integration defines each connection/capability; Execution decides which
/// capabilities belong to the route.
pub struct ExecutionConnections {
    pub descriptor: Option<ConnectionDescriptor>,
    pub descriptors: Vec<ConnectionDescriptor>,
    /// Explicit synchronous projection for CLI/offline callers and providers
    /// whose native async slice has not migrated yet. Production live routes
    /// with async capabilities leave this empty; the process installs its
    /// bounded Actor proxy before accepting requests.
    pub order_entry: Option<Box<dyn OrderEntryConnection>>,
    pub order_query: Option<Box<dyn OrderQueryConnection>>,
    pub execution_stream: Option<Box<dyn OrderEventSource>>,
    pub async_order_entry: Option<ExecutionAsyncOrderEntryRoutes>,
    pub async_order_query: Option<ExecutionAsyncOrderQueryRoutes>,
    pub async_execution_streams: Vec<ExecutionAsyncRoute<ExecutionAsyncEventSource>>,
}

pub struct DirectExecutionConnections {
    pub(super) order_entry: Option<Box<dyn OrderEntryConnection>>,
    pub(super) order_query: Option<Box<dyn OrderQueryConnection>>,
    pub(super) execution_stream: Option<Box<dyn OrderEventSource>>,
    pub(super) runtime: DirectExecutionRuntime,
}

impl DirectExecutionConnections {
    pub fn into_parts(
        self,
    ) -> (
        Option<Box<dyn OrderEntryConnection>>,
        Option<Box<dyn OrderQueryConnection>>,
        Option<Box<dyn OrderEventSource>>,
        DirectExecutionRuntime,
    ) {
        (
            self.order_entry,
            self.order_query,
            self.execution_stream,
            self.runtime,
        )
    }
}

pub struct DirectExecutionRuntime {
    pub(super) shutdown: Option<tokio::sync::watch::Sender<bool>>,
    pub(super) _tasks: Vec<tokio::task::JoinHandle<()>>,
}

impl DirectExecutionRuntime {
    pub(super) fn none() -> Self {
        Self {
            shutdown: None,
            _tasks: Vec::new(),
        }
    }
}

/// Load the active Reference-owned execution address book. Production
/// composition installs this before accepting submissions; Execution never
/// reconstructs provider symbols or product discriminators from business IDs.
pub fn load_reference_execution_accesses(
    database: &Path,
) -> Result<Vec<(kairos_primitives::ExecutionAccessId, ProviderInstrumentRef)>, String> {
    let endpoint = kairos_reference_contract::ReferenceEndpoint {
        database: database.to_path_buf(),
        actor_id: "reference-actor".into(),
        aeron_dir: None,
        aeron_channel: kairos_transport::DEFAULT_CHANNEL.into(),
        event_stream_id: kairos_transport::stream_ids::REFERENCE_CHANGES,
    };
    let snapshot = kairos_reference_contract::ReferenceClient::connect(endpoint)
        .execution_snapshot()
        .map_err(|error| error.to_string())?;
    snapshot
        .execution_accesses
        .iter()
        .filter(|access| matches!(access.status.as_str(), "active" | "trading"))
        .map(|access| {
            provider_instrument_from_execution_access(
                &access.access_id,
                &access.provider_id,
                &access.provider_product,
                &access.provider_symbol,
            )
        })
        .collect()
}

pub(super) fn provider_instrument_from_execution_access(
    access_id: &str,
    provider_id: &str,
    provider_product: &str,
    provider_symbol: &str,
) -> Result<(kairos_primitives::ExecutionAccessId, ProviderInstrumentRef), String> {
    let participant_kind = match provider_id {
        "binance" | "okx" | "hyperliquid" => ParticipantKind::Exchange,
        "ibkr" => ParticipantKind::Broker,
        provider => {
            return Err(format!(
                "unsupported execution-access provider in Reference: {provider}"
            ))
        }
    };
    Ok((
        kairos_primitives::ExecutionAccessId::new(access_id).map_err(|error| error.to_string())?,
        ProviderInstrumentRef::new(
            ParticipantRef::new(participant_kind, provider_id)
                .map_err(|error| error.to_string())?,
            Some(
                ParticipantInstrumentTypeRef::new(provider_product)
                    .map_err(|error| error.to_string())?,
            ),
            provider_symbol,
        )
        .map_err(|error| error.to_string())?,
    ))
}

impl Drop for DirectExecutionRuntime {
    fn drop(&mut self) {
        if let Some(shutdown) = self.shutdown.take() {
            let _ = shutdown.send(true);
        }
    }
}
