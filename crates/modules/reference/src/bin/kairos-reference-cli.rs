//! Reference operator and verification CLI.

use std::collections::VecDeque;
use std::fs::File;
use std::io::{BufRead, BufReader, Seek, SeekFrom};
use std::path::Path;
use std::str::FromStr;
use std::time::Duration;

use clap::{Args, Parser, Subcommand};
use kairos_primitives::DomainTypeError;
use kairos_primitives::reference::{
    AssetId, ExchangeId, InstrumentId, ListingId, ReferenceSourceId, Symbol,
};
use kairos_primitives::time::UnixNanos;
use kairos_reference::application::{
    CliReferenceApplication, ConnectedReferenceApplication, ConnectedReferenceOutput,
    ReferenceCatalogCollection, ReferenceCatalogListRequest, ReferenceCliOutput, ReferenceKind,
    ReferenceMarketCatalogRequest, ReferenceOptionChainRequest, ReferenceQuery,
};
use kairos_reference_contract::{
    BinanceReferenceSource, HyperliquidReferenceSource, MassiveReferenceSource, OkxReferenceSource,
    ReferenceSourceBinding, ReferenceSourceControlRequest, ReferenceSourceDefinitionRequest,
    ReferenceSourceDesiredState, ReferenceSourceScope, ReferenceSourceScopeKind,
    ReferenceUpsertConflictPolicy, ReferenceUpsertProvenance, UpsertAssetRequest,
    UpsertInstrumentRequest, UpsertListingRequest,
};
use kairos_workspace::JsonRpcControlClient;
use kairos_workspace::cli::{OutputFormat, render};
use kairos_workspace::workspace::Workspace;
use serde_json::{Value, json};

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Cli::parse();
    let workspace = Workspace::open(&args.workspace)?;
    let output = args.output.unwrap_or_else(|| {
        workspace
            .cli_format()
            .parse()
            .expect("workspace output format validated when opened")
    });
    match args.command {
        Command::Standalone(command) => execute_standalone_cli(&workspace, command, output).await?,
        Command::Connected(command) => execute_connected_cli(&workspace, command, output).await?,
    }
    Ok(())
}

async fn execute_standalone_cli(
    workspace: &Workspace,
    command: StandaloneCommand,
    output: OutputFormat,
) -> Result<(), Box<dyn std::error::Error>> {
    let app = CliReferenceApplication::open(workspace)?;
    let value = execute_standalone_read(&app, command)?;
    println!("{}", render(&value, output));
    Ok(())
}

async fn execute_connected_cli(
    workspace: &Workspace,
    command: ConnectedCommand,
    output: OutputFormat,
) -> Result<(), Box<dyn std::error::Error>> {
    match command {
        ConnectedCommand::Logs(args) if args.follow => {
            follow_logs(
                &workspace.logs_root().join("reference/process.log"),
                args,
                output,
            )?;
            return Ok(());
        },
        command if connected_requires_runtime_control(&command) => {
            let value = execute_connected_runtime_control(&workspace, command).await?;
            println!("{}", render(&value, output));
            return Ok(());
        },
        command => {
            let app = CliReferenceApplication::open(workspace)?;
            let server = connected_reference_app(workspace)?;
            let value = execute_connected_read(workspace, &server, &app, command).await?;
            println!("{}", render(&value, output));
        },
    }
    Ok(())
}

fn execute_standalone_read(
    app: &CliReferenceApplication,
    command: StandaloneCommand,
) -> Result<ReferenceCliOutput, Box<dyn std::error::Error>> {
    let value = match command {
        StandaloneCommand::Snapshot => app.catalog_status()?,
        StandaloneCommand::Catalog { command } => match command {
            CatalogCommand::Exchanges(args) => app.catalog_collection(
                ReferenceCatalogCollection::Exchanges,
                catalog_list_request(args),
            )?,
            CatalogCommand::Assets(args) => app.catalog_collection(
                ReferenceCatalogCollection::Assets,
                catalog_list_request(args),
            )?,
            CatalogCommand::Instruments(args) => app.catalog_collection(
                ReferenceCatalogCollection::Instruments,
                catalog_list_request(args),
            )?,
            CatalogCommand::Listings(args) => app.catalog_collection(
                ReferenceCatalogCollection::Listings,
                catalog_list_request(args),
            )?,
            CatalogCommand::Markets(args) => app.markets(market_catalog_request(args), false)?,
            CatalogCommand::Show { identifier } => app.show_catalog_record(&identifier)?,
        },
        StandaloneCommand::Assets { command } => match command {
            StandaloneAssetCommand::List(args) => app.list_assets(asset_list_request(args))?,
            StandaloneAssetCommand::Show { asset_id } => app.show_asset(&asset_id)?,
        },
        StandaloneCommand::Markets { command } => {
            let (args, resolve) = match command {
                MarketCommand::List(args) | MarketCommand::Browse(args) => (args, false),
                MarketCommand::Resolve(args) => (args, true),
            };
            app.markets(market_catalog_request(args), resolve)?
        },
        StandaloneCommand::OptionChain(args) => app.option_chain(option_chain_request(args))?,
        StandaloneCommand::Query(args) => app.query(args.kind(), args.try_into_query()?)?,
        StandaloneCommand::Search(args) => app.search(args.text, args.limit)?,
        StandaloneCommand::Show { identifier } => app.show_catalog_record(&identifier)?,
    };
    Ok(value)
}

fn catalog_list_request(args: CatalogListArgs) -> ReferenceCatalogListRequest {
    ReferenceCatalogListRequest {
        query: args.query,
        status: args.status,
        active_only: args.active_only,
        limit: args.limit,
    }
}

fn asset_list_request(args: AssetListArgs) -> ReferenceCatalogListRequest {
    ReferenceCatalogListRequest {
        query: args.query,
        status: args.status,
        active_only: args.active_only,
        limit: args.limit,
    }
}

fn market_catalog_request(args: MarketQueryArgs) -> ReferenceMarketCatalogRequest {
    ReferenceMarketCatalogRequest {
        symbol: args.symbol,
        market_id: args.market_id,
        exchange_id: args.exchange_id.or(args.exchange),
        instrument_kind: args.instrument_kind.or(args.market),
        asset_type: args.asset_type,
        status: args.status,
        limit: args.limit,
        active_only: args.active_only,
    }
}

fn option_chain_request(args: OptionChainArgs) -> ReferenceOptionChainRequest {
    ReferenceOptionChainRequest {
        underlying_instrument_id: args.underlying_instrument_id,
        expiry_unix_nanos: args.expiry_unix_nanos,
        expiry_from_unix_nanos: args.expiry_from_unix_nanos,
        expiry_to_unix_nanos: args.expiry_to_unix_nanos,
        option_right: args.option_right,
        active_only: args.active_only,
        limit: args.limit.unwrap_or(256),
    }
}

async fn execute_connected_read(
    workspace: &Workspace,
    server: &ConnectedReferenceApplication<JsonRpcControlClient>,
    app: &CliReferenceApplication,
    command: ConnectedCommand,
) -> Result<ConnectedReferenceOutput, Box<dyn std::error::Error>> {
    match command {
        ConnectedCommand::Status(args) => {
            if args.catalog {
                return Ok(ConnectedReferenceOutput::LocalCatalog(
                    app.catalog_status()?,
                ));
            }
            Ok(ConnectedReferenceOutput::Status(server.status().await?))
        },
        ConnectedCommand::Providers(args) => {
            let source_filter = args.source_filter()?;
            let show = args.is_show();
            let status = server.runtime_status().await?;
            Ok(ConnectedReferenceOutput::Providers(
                ConnectedReferenceApplication::<JsonRpcControlClient>::summarize_providers(
                    status,
                    source_filter,
                    show,
                )?,
            ))
        },
        ConnectedCommand::Doctor => {
            let status = server.runtime_status().await?;
            Ok(ConnectedReferenceOutput::Doctor(
                ConnectedReferenceApplication::<JsonRpcControlClient>::summarize_doctor(status)?,
            ))
        },
        ConnectedCommand::Logs(args) => Ok(ConnectedReferenceOutput::Diagnostic(read_logs(
            &workspace.logs_root().join("reference/process.log"),
            args,
        )?)),
        ConnectedCommand::Coverage(args) => {
            if args.command.is_some() {
                unreachable!("coverage mutation routed to runtime control");
            }
            let status = server.runtime_status().await?;
            Ok(ConnectedReferenceOutput::Coverage(
                ConnectedReferenceApplication::<JsonRpcControlClient>::summarize_coverage(status),
            ))
        },
        _ => unreachable!("runtime control command routed before connected read"),
    }
}

fn connected_reference_app(
    workspace: &Workspace,
) -> Result<ConnectedReferenceApplication<JsonRpcControlClient>, Box<dyn std::error::Error>> {
    let client = JsonRpcControlClient::new(workspace.process_socket("reference")?);
    Ok(ConnectedReferenceApplication::connect(client))
}

fn connected_requires_runtime_control(command: &ConnectedCommand) -> bool {
    match command {
        ConnectedCommand::Providers(args) => args.is_control(),
        ConnectedCommand::Coverage(args) => args.command.is_some(),
        ConnectedCommand::Refresh(_)
        | ConnectedCommand::Sync(_)
        | ConnectedCommand::Publish
        | ConnectedCommand::Assets { .. }
        | ConnectedCommand::Instruments { .. }
        | ConnectedCommand::Listings { .. } => true,
        ConnectedCommand::Status(_) | ConnectedCommand::Doctor | ConnectedCommand::Logs(_) => false,
    }
}

async fn execute_connected_runtime_control(
    workspace: &Workspace,
    command: ConnectedCommand,
) -> Result<ConnectedReferenceOutput, Box<dyn std::error::Error>> {
    let server = connected_reference_app(workspace)?;
    let value = match command {
        ConnectedCommand::Refresh(args) | ConnectedCommand::Sync(args) => {
            let source_id = args
                .source
                .map(ReferenceSourceId::new)
                .transpose()
                .map_err(|error| format!("invalid source id: {error}"))?;
            ConnectedReferenceOutput::Refresh(server.refresh(source_id).await?)
        },
        ConnectedCommand::Publish => ConnectedReferenceOutput::Publish(server.publish().await?),
        ConnectedCommand::Coverage(args) => match args.command {
            Some(CoverageCommand::Add { underlying }) => ConnectedReferenceOutput::OptionCoverage(
                server
                    .add_option_coverage(InstrumentId::try_from(underlying)?)
                    .await?,
            ),
            Some(CoverageCommand::Remove { underlying }) => {
                ConnectedReferenceOutput::OptionCoverage(
                    server
                        .remove_option_coverage(InstrumentId::try_from(underlying)?)
                        .await?,
                )
            },
            None => unreachable!("read-only coverage command routed before runtime control"),
        },
        ConnectedCommand::Providers(args) if args.is_control() => match args.control_command()? {
            ProviderControlCommand::Add => ConnectedReferenceOutput::SourceStatus(
                server
                    .upsert_source_definition(args.source_definition_request()?)
                    .await?,
            ),
            command => {
                let source_id = ReferenceSourceId::new(args.control_source_id()?)
                    .map_err(|error| format!("invalid source id: {error}"))?;
                ConnectedReferenceOutput::SourceStatus(
                    server
                        .set_source_desired_state(ReferenceSourceControlRequest {
                            source_id,
                            desired_state: command.desired_state(),
                        })
                        .await?,
                )
            },
        },
        ConnectedCommand::Assets {
            command: ConnectedAssetCommand::Add(args),
        } => ConnectedReferenceOutput::Mutation(
            server.upsert_asset(upsert_asset_request(args)?).await?,
        ),
        ConnectedCommand::Instruments {
            command: InstrumentCommand::Add(args),
        } => ConnectedReferenceOutput::Mutation(
            server
                .upsert_instrument(upsert_instrument_request(args)?)
                .await?,
        ),
        ConnectedCommand::Listings {
            command: ListingCommand::Add(args),
        } => ConnectedReferenceOutput::Mutation(
            server.upsert_listing(upsert_listing_request(args)?).await?,
        ),
        _ => unreachable!("runtime control command checked by caller"),
    };
    Ok(value)
}

fn upsert_asset_request(
    args: AddAssetArgs,
) -> Result<UpsertAssetRequest, Box<dyn std::error::Error>> {
    Ok(UpsertAssetRequest {
        asset_id: AssetId::try_from(args.asset_id)?,
        code: Symbol::try_from(args.code)?,
        name: args.name,
        asset_class: args.asset_class.parse()?,
        status: args.status.into(),
        provenance: ReferenceUpsertProvenance::Manual,
        conflict_policy: ReferenceUpsertConflictPolicy::RejectProviderOwned,
    })
}

fn upsert_instrument_request(
    args: AddInstrumentArgs,
) -> Result<UpsertInstrumentRequest, Box<dyn std::error::Error>> {
    Ok(UpsertInstrumentRequest {
        instrument_id: InstrumentId::try_from(args.instrument_id)?,
        symbol: Symbol::try_from(args.symbol)?,
        name: args.name,
        instrument_type: args.instrument_type.parse()?,
        underlying_instrument_id: args
            .underlying_instrument_id
            .map(InstrumentId::try_from)
            .transpose()?,
        expiry_unix_nanos: args.expiry_unix_nanos.map(UnixNanos::from),
        strike: args.strike.map(|value| value.parse()).transpose()?,
        option_right: args.option_right,
        status: args.status.into(),
        provenance: ReferenceUpsertProvenance::Manual,
        conflict_policy: ReferenceUpsertConflictPolicy::RejectProviderOwned,
        issuer_id: None,
        share_class: None,
        primary_currency_asset_id: None,
    })
}

fn upsert_listing_request(
    args: AddListingArgs,
) -> Result<UpsertListingRequest, Box<dyn std::error::Error>> {
    Ok(UpsertListingRequest {
        listing_id: ListingId::try_from(args.listing_id)?,
        instrument_id: InstrumentId::try_from(args.instrument_id)?,
        exchange_id: ExchangeId::new(args.exchange_id)?,
        exchange_symbol: Symbol::new(args.exchange_symbol)?,
        status: args.status.into(),
        effective_from_unix_nanos: args.effective_from_unix_nanos.into(),
        effective_to_unix_nanos: args.effective_to_unix_nanos.map(UnixNanos::from),
        provenance: ReferenceUpsertProvenance::Manual,
        conflict_policy: ReferenceUpsertConflictPolicy::RejectProviderOwned,
    })
}

fn read_logs(log_path: &Path, args: LogsArgs) -> Result<Value, Box<dyn std::error::Error>> {
    let limit = args.limit.clamp(1, 10_000);
    if !log_path.exists() {
        return Ok(json!([{
            "level": "WARN",
            "event": "logs.unavailable",
            "area": "startup",
            "outcome": "skipped",
            "summary": format!("reference process log not found at {}", log_path.display()),
        }]));
    }

    let file = File::open(log_path)?;
    let reader = BufReader::new(file);
    let mut entries = VecDeque::with_capacity(limit.saturating_add(1));
    for line in reader.lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let Ok(value) = serde_json::from_str::<Value>(&line) else {
            continue;
        };
        if !matches_log_entry(&value, &args) {
            continue;
        }
        if entries.len() == limit {
            entries.pop_front();
        }
        entries.push_back(value);
    }

    if args.tick.as_deref() == Some("latest") {
        let latest_tick = entries
            .iter()
            .rev()
            .find_map(|entry| string_field(entry, "tick_id").map(str::to_owned));
        if let Some(latest_tick) = latest_tick {
            entries.retain(|entry| string_field(entry, "tick_id") == Some(latest_tick.as_str()));
        }
    }

    let values = entries
        .iter()
        .map(|entry| {
            if args.json {
                entry.clone()
            } else {
                summarize_log_entry(entry)
            }
        })
        .collect::<Vec<_>>();
    Ok(json!(values))
}

fn follow_logs(
    log_path: &Path,
    args: LogsArgs,
    output: OutputFormat,
) -> Result<(), Box<dyn std::error::Error>> {
    let limit = args.limit.clamp(1, 10_000);
    if !log_path.exists() {
        println!(
            "{}",
            render(
                &json!({
                    "level": "WARN",
                    "event": "logs.unavailable",
                    "area": "startup",
                    "outcome": "skipped",
                    "summary": format!("reference process log not found at {}; waiting", log_path.display()),
                }),
                output,
            )
        );
    }

    let mut position = 0;
    if log_path.exists() {
        let file = File::open(log_path)?;
        let mut reader = BufReader::new(file);
        let mut entries = VecDeque::with_capacity(limit.saturating_add(1));
        let mut line = String::new();
        while reader.read_line(&mut line)? > 0 {
            let Some(value) = parse_log_line(&line) else {
                line.clear();
                continue;
            };
            if matches_log_entry(&value, &args) {
                if entries.len() == limit {
                    entries.pop_front();
                }
                entries.push_back(value);
            }
            line.clear();
        }
        position = reader.stream_position()?;
        if args.tick.as_deref() == Some("latest") {
            let latest_tick = entries
                .iter()
                .rev()
                .find_map(|entry| string_field(entry, "tick_id").map(str::to_owned));
            if let Some(latest_tick) = latest_tick {
                entries
                    .retain(|entry| string_field(entry, "tick_id") == Some(latest_tick.as_str()));
            }
        }
        for entry in entries {
            print_log_entry(&entry, &args, output);
        }
    }

    loop {
        if !log_path.exists() {
            std::thread::sleep(Duration::from_millis(750));
            continue;
        }
        let mut file = File::open(log_path)?;
        let file_len = file.metadata()?.len();
        if position > file_len {
            position = 0;
        }
        file.seek(SeekFrom::Start(position))?;
        let mut reader = BufReader::new(file);
        let mut line = String::new();
        while reader.read_line(&mut line)? > 0 {
            if let Some(value) = parse_log_line(&line) {
                if matches_log_entry(&value, &args) {
                    print_log_entry(&value, &args, output);
                }
            }
            line.clear();
        }
        position = reader.into_inner().stream_position()?;
        std::thread::sleep(Duration::from_millis(750));
    }
}

fn parse_log_line(line: &str) -> Option<Value> {
    if line.trim().is_empty() {
        return None;
    }
    serde_json::from_str::<Value>(line).ok()
}

fn print_log_entry(value: &Value, args: &LogsArgs, output: OutputFormat) {
    let value = if args.json {
        value.clone()
    } else {
        summarize_log_entry(value)
    };
    println!("{}", render(&value, output));
}

fn matches_log_entry(value: &Value, args: &LogsArgs) -> bool {
    if args.errors && !is_error_log(value) {
        return false;
    }
    if let Some(source) = args.source.as_deref() {
        let entry_source =
            string_field(value, "source_id").or_else(|| string_field(value, "provider"));
        if entry_source != Some(source) {
            return false;
        }
    }
    if let Some(tick) = args.tick.as_deref() {
        if tick != "latest" && string_field(value, "tick_id") != Some(tick) {
            return false;
        }
    }
    true
}

fn is_error_log(value: &Value) -> bool {
    matches!(
        string_field(value, "level"),
        Some("WARN" | "ERROR" | "warn" | "error")
    ) || matches!(string_field(value, "outcome"), Some("degraded" | "failed"))
        || value.get("error").is_some()
        || value.get("error_kind").is_some()
}

fn summarize_log_entry(value: &Value) -> Value {
    let raw_event = string_field(value, "event").unwrap_or("unknown");
    let (area, action, outcome) = event_layers(value, raw_event);
    let source = string_field(value, "source_id").or_else(|| string_field(value, "provider"));
    let mut summary = serde_json::Map::new();
    summary.insert("time".to_owned(), json!(short_timestamp(value)));
    summary.insert(
        "level".to_owned(),
        json!(string_field(value, "level").unwrap_or("INFO")),
    );
    summary.insert("area".to_owned(), json!(area));
    summary.insert(
        "event".to_owned(),
        json!(format!("{area}.{action}.{outcome}")),
    );
    summary.insert("outcome".to_owned(), json!(outcome));
    if let Some(tick_id) = string_field(value, "tick_id") {
        summary.insert("tick".to_owned(), json!(tick_id));
    }
    if let Some(source) = source {
        summary.insert("source".to_owned(), json!(source));
    }
    if let Some(progress) = progress_summary(value) {
        summary.insert("progress".to_owned(), json!(progress));
    }
    summary.insert("summary".to_owned(), json!(safe_summary(value)));
    if raw_event.contains('_') {
        summary.insert("legacy_event".to_owned(), json!(raw_event));
    }
    Value::Object(summary)
}

fn event_layers<'a>(value: &'a Value, raw_event: &'a str) -> (&'a str, &'a str, &'a str) {
    if let (Some(area), Some(action), Some(outcome)) = (
        string_field(value, "area"),
        string_field(value, "action"),
        string_field(value, "outcome"),
    ) {
        return (area, action, outcome);
    }
    match raw_event {
        "process_spawned" => ("startup", "stage", "started"),
        "logger_initialized" | "reference_state_loaded" => ("startup", "stage", "completed"),
        "reference_startup_integrity_repair" => ("startup", "stage", "degraded"),
        "reference_provider_scan_reset" => ("source", "work", "started"),
        "reference_refresh_started" => ("app", "tick", "started"),
        "reference_refresh_completed" => ("app", "tick", "completed"),
        "reference_reconcile_completed" => ("reconcile", "apply", "completed"),
        "reference_provider_scan_completed" => ("source", "scan", "completed"),
        "reference_provider_sync_in_progress" => ("source", "scan", "progress"),
        "reference_provider_degraded" => ("source", "scan", "degraded"),
        "reference_events_acknowledged" => ("publication", "publish", "completed"),
        "json_rpc_connection_failed" => ("rpc", "call", "failed"),
        _ => ("app", "tick", "progress"),
    }
}

fn short_timestamp(value: &Value) -> String {
    string_field(value, "timestamp")
        .and_then(|timestamp| timestamp.split_once('T').map(|(_, time)| time))
        .map(|time| time.trim_end_matches('Z'))
        .map(|time| time.split('.').next().unwrap_or(time))
        .unwrap_or("")
        .to_owned()
}

fn progress_summary(value: &Value) -> Option<String> {
    let mut parts = Vec::new();
    if let Some(page_count) = value.get("page_count").and_then(Value::as_u64) {
        parts.push(format!("pages={page_count}"));
    }
    if let Some(records_seen) = value.get("records_seen").and_then(Value::as_u64) {
        parts.push(format!("records={records_seen}"));
    }
    if let Some(duration_ms) = value.get("duration_ms").and_then(Value::as_u64) {
        parts.push(format!("{duration_ms}ms"));
    }
    if let Some(generation) = value.get("generation").and_then(Value::as_u64) {
        parts.push(format!("gen={generation}"));
    }
    if parts.is_empty() {
        None
    } else {
        Some(parts.join(" "))
    }
}

fn safe_summary(value: &Value) -> String {
    let message = string_field(value, "safe_message")
        .or_else(|| string_field(value, "message"))
        .or_else(|| string_field(value, "error"))
        .unwrap_or("");
    truncate_for_log_view(message, 160)
}

fn truncate_for_log_view(value: &str, max_len: usize) -> String {
    if value.len() <= max_len {
        return value.to_owned();
    }
    let mut truncated = value
        .char_indices()
        .take_while(|(index, _)| *index < max_len)
        .map(|(_, character)| character)
        .collect::<String>();
    truncated.push_str("...");
    truncated
}

fn string_field<'a>(value: &'a Value, field: &str) -> Option<&'a str> {
    value.get(field).and_then(Value::as_str)
}

impl ProvidersArgs {
    fn is_control_command(command: &ProviderCommand) -> bool {
        matches!(
            command,
            ProviderCommand::Pause { .. }
                | ProviderCommand::Resume { .. }
                | ProviderCommand::Disable { .. }
                | ProviderCommand::Enable { .. }
                | ProviderCommand::Add(_)
        )
    }

    fn source_filter(&self) -> Result<Option<String>, Box<dyn std::error::Error>> {
        match (&self.source, &self.command) {
            (Some(_), Some(ProviderCommand::Show { .. })) => Err(
                "use either `providers --source <id>` or `providers show <id>`, not both".into(),
            ),
            (Some(_), Some(command)) if Self::is_control_command(command) => Err(
                "`providers --source <id>` cannot be combined with provider control commands"
                    .into(),
            ),
            (Some(source), None) => Ok(Some(source.clone())),
            (None, Some(ProviderCommand::Show { source_id })) => Ok(Some(source_id.clone())),
            (None, Some(command)) if Self::is_control_command(command) => Ok(None),
            (None, None) => Ok(None),
            _ => Err("unsupported providers command combination".into()),
        }
    }

    fn is_show(&self) -> bool {
        matches!(self.command, Some(ProviderCommand::Show { .. }))
    }

    fn is_control(&self) -> bool {
        self.command.as_ref().is_some_and(Self::is_control_command)
    }

    fn control_command(&self) -> Result<ProviderControlCommand, Box<dyn std::error::Error>> {
        match &self.command {
            Some(ProviderCommand::Add(_)) => Ok(ProviderControlCommand::Add),
            Some(ProviderCommand::Pause { .. }) => Ok(ProviderControlCommand::Pause),
            Some(ProviderCommand::Resume { .. }) => Ok(ProviderControlCommand::Resume),
            Some(ProviderCommand::Disable { .. }) => Ok(ProviderControlCommand::Disable),
            Some(ProviderCommand::Enable { .. }) => Ok(ProviderControlCommand::Enable),
            _ => Err("provider control command requires an action".into()),
        }
    }

    fn control_source_id(&self) -> Result<String, Box<dyn std::error::Error>> {
        match (&self.source, &self.command) {
            (Some(_), Some(command)) if Self::is_control_command(command) => Err(
                "`providers --source <id>` cannot be combined with provider control commands"
                    .into(),
            ),
            (None, Some(ProviderCommand::Pause { source_id })) => Ok(source_id.clone()),
            (None, Some(ProviderCommand::Resume { source_id })) => Ok(source_id.clone()),
            (None, Some(ProviderCommand::Disable { source_id })) => Ok(source_id.clone()),
            (None, Some(ProviderCommand::Enable { source_id })) => Ok(source_id.clone()),
            (None, Some(ProviderCommand::Add(_))) => {
                Err("provider add command does not use a separate control source id".into())
            },
            _ => Err("provider control command requires a source id".into()),
        }
    }

    fn source_definition_request(
        &self,
    ) -> Result<ReferenceSourceDefinitionRequest, Box<dyn std::error::Error>> {
        let Some(ProviderCommand::Add(args)) = &self.command else {
            return Err("provider add command requires source definition arguments".into());
        };
        Ok(args.source_definition_request()?)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ProviderControlCommand {
    Add,
    Pause,
    Resume,
    Disable,
    Enable,
}

impl ProviderControlCommand {
    const fn desired_state(self) -> ReferenceSourceDesiredState {
        match self {
            Self::Add => ReferenceSourceDesiredState::Enabled,
            Self::Pause => ReferenceSourceDesiredState::Paused,
            Self::Resume | Self::Enable => ReferenceSourceDesiredState::Enabled,
            Self::Disable => ReferenceSourceDesiredState::Disabled,
        }
    }
}

impl AddProviderArgs {
    fn source_definition_request(
        &self,
    ) -> Result<ReferenceSourceDefinitionRequest, Box<dyn std::error::Error>> {
        Ok(ReferenceSourceDefinitionRequest {
            binding: parse_source_binding(&self.provider, &self.source)?,
            scope: ReferenceSourceScope {
                kind: parse_source_scope_kind(&self.scope_kind)?,
                id: self.scope_id.clone(),
            },
            desired_state: parse_source_desired_state(&self.desired_state)?,
            credential_binding: self.credential_binding.clone(),
        })
    }
}

fn parse_source_binding(
    provider: &str,
    source: &str,
) -> Result<ReferenceSourceBinding, Box<dyn std::error::Error>> {
    let provider = provider.trim().to_ascii_lowercase();
    let source = source.trim().to_ascii_lowercase();
    match (provider.as_str(), source.as_str()) {
        ("binance", "spot") => Ok(ReferenceSourceBinding::Binance(
            BinanceReferenceSource::Spot,
        )),
        ("binance", "usd-m-futures" | "usdm-futures") => Ok(ReferenceSourceBinding::Binance(
            BinanceReferenceSource::UsdMFutures,
        )),
        ("binance", "coin-m-futures" | "coinm-futures") => Ok(ReferenceSourceBinding::Binance(
            BinanceReferenceSource::CoinMFutures,
        )),
        ("binance", "options") => Ok(ReferenceSourceBinding::Binance(
            BinanceReferenceSource::Options,
        )),
        ("binance", "equity") => Ok(ReferenceSourceBinding::Binance(
            BinanceReferenceSource::Equity,
        )),
        ("okx", "spot") => Ok(ReferenceSourceBinding::Okx(OkxReferenceSource::Spot)),
        ("okx", "margin") => Ok(ReferenceSourceBinding::Okx(OkxReferenceSource::Margin)),
        ("okx", "swap") => Ok(ReferenceSourceBinding::Okx(OkxReferenceSource::Swap)),
        ("okx", "futures") => Ok(ReferenceSourceBinding::Okx(OkxReferenceSource::Futures)),
        ("okx", "options") => Ok(ReferenceSourceBinding::Okx(OkxReferenceSource::Options)),
        ("hyperliquid", "spot") => Ok(ReferenceSourceBinding::Hyperliquid(
            HyperliquidReferenceSource::Spot,
        )),
        ("hyperliquid", "perpetual") => Ok(ReferenceSourceBinding::Hyperliquid(
            HyperliquidReferenceSource::Perpetual,
        )),
        ("massive", "equity") => Ok(ReferenceSourceBinding::Massive(
            MassiveReferenceSource::Equity,
        )),
        ("massive", "options") => Ok(ReferenceSourceBinding::Massive(
            MassiveReferenceSource::Options,
        )),
        _ => Err(format!(
            "unsupported Reference source binding: provider={provider} source={source}"
        )
        .into()),
    }
}

fn parse_source_desired_state(
    value: &str,
) -> Result<ReferenceSourceDesiredState, Box<dyn std::error::Error>> {
    match value.trim().to_ascii_lowercase().as_str() {
        "enabled" => Ok(ReferenceSourceDesiredState::Enabled),
        "disabled" => Ok(ReferenceSourceDesiredState::Disabled),
        "paused" => Ok(ReferenceSourceDesiredState::Paused),
        "removed" => Ok(ReferenceSourceDesiredState::Removed),
        other => Err(format!("unsupported source desired state: {other}").into()),
    }
}

fn parse_source_scope_kind(
    value: &str,
) -> Result<ReferenceSourceScopeKind, Box<dyn std::error::Error>> {
    match value.trim().to_ascii_lowercase().as_str() {
        "global" => Ok(ReferenceSourceScopeKind::Global),
        "provider_catalog" => Ok(ReferenceSourceScopeKind::ProviderCatalog),
        "underlying_instrument" => Ok(ReferenceSourceScopeKind::UnderlyingInstrument),
        "coverage" => Ok(ReferenceSourceScopeKind::Coverage),
        "custom" => Ok(ReferenceSourceScopeKind::Custom),
        other => Err(format!("unsupported source scope kind: {other}").into()),
    }
}

#[derive(Debug, Parser)]
#[command(
    name = "kairos-reference-cli",
    about = "Reference operator and verification CLI"
)]
struct Cli {
    #[arg(long)]
    workspace: std::path::PathBuf,
    #[arg(long, global = true, visible_alias = "format", value_parser = OutputFormat::from_str)]
    output: Option<OutputFormat>,
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    #[command(name = "standalone", subcommand)]
    Standalone(StandaloneCommand),
    #[command(name = "connected", subcommand)]
    Connected(ConnectedCommand),
}

#[derive(Debug, Subcommand)]
enum StandaloneCommand {
    Snapshot,
    Assets {
        #[command(subcommand)]
        command: StandaloneAssetCommand,
    },
    Catalog {
        #[command(subcommand)]
        command: CatalogCommand,
    },
    Markets {
        #[command(subcommand)]
        command: MarketCommand,
    },
    OptionChain(OptionChainArgs),
    Query(QueryArgs),
    Search(SearchArgs),
    Show {
        identifier: String,
    },
}

#[derive(Debug, Subcommand)]
enum StandaloneAssetCommand {
    List(AssetListArgs),
    Show { asset_id: String },
}

#[derive(Debug, Subcommand)]
enum ConnectedCommand {
    Status(StatusArgs),
    Providers(ProvidersArgs),
    Doctor,
    Logs(LogsArgs),
    Coverage(CoverageArgs),
    Refresh(RefreshArgs),
    #[command(about = "Compatibility alias for refresh; prefer `refresh`")]
    Sync(RefreshArgs),
    Publish,
    Assets {
        #[command(subcommand)]
        command: ConnectedAssetCommand,
    },
    Instruments {
        #[command(subcommand)]
        command: InstrumentCommand,
    },
    Listings {
        #[command(subcommand)]
        command: ListingCommand,
    },
}

#[derive(Debug, Subcommand)]
enum ConnectedAssetCommand {
    Add(AddAssetArgs),
}

#[derive(Debug, Args)]
struct StatusArgs {
    #[arg(
        long,
        help = "Read the catalog directly instead of asking the Reference server"
    )]
    catalog: bool,
}

#[derive(Debug, Args)]
struct ProvidersArgs {
    #[arg(long, help = "Show one Reference source/provider")]
    source: Option<String>,
    #[command(subcommand)]
    command: Option<ProviderCommand>,
}

#[derive(Debug, Subcommand)]
enum ProviderCommand {
    Show { source_id: String },
    Add(AddProviderArgs),
    Pause { source_id: String },
    Resume { source_id: String },
    Disable { source_id: String },
    Enable { source_id: String },
}

#[derive(Debug, Args)]
struct AddProviderArgs {
    /// Provider owning the advanced Reference source, for example `massive`.
    provider: String,
    /// Provider-scoped Reference source, for example `options`.
    source: String,
    #[arg(long, default_value = "enabled")]
    desired_state: String,
    #[arg(long, default_value = "global")]
    scope_kind: String,
    #[arg(long)]
    scope_id: Option<String>,
    #[arg(long)]
    credential_binding: Option<String>,
}

#[derive(Debug, Args)]
struct CoverageArgs {
    #[command(subcommand)]
    command: Option<CoverageCommand>,
}

#[derive(Debug, Subcommand)]
enum CoverageCommand {
    Add { underlying: String },
    Remove { underlying: String },
}

#[derive(Debug, Args)]
struct RefreshArgs {
    #[arg(long, help = "Advance only one configured Reference source")]
    source: Option<String>,
}

#[derive(Debug, Args)]
struct LogsArgs {
    #[arg(long, help = "Continue streaming matching log entries")]
    follow: bool,
    #[arg(
        long,
        help = "Show only warning, error, degraded, or failed log entries"
    )]
    errors: bool,
    #[arg(long, help = "Show entries for one Reference source/provider")]
    source: Option<String>,
    #[arg(long, help = "Show entries for one tick id, or 'latest'")]
    tick: Option<String>,
    #[arg(
        long,
        default_value_t = 100,
        help = "Maximum number of matching entries"
    )]
    limit: usize,
    #[arg(long, help = "Return filtered raw JSON log entries")]
    json: bool,
}

#[derive(Debug, Subcommand)]
enum AssetCommand {
    Add(AddAssetArgs),
    List(AssetListArgs),
    Show { asset_id: String },
}

#[derive(Debug, Subcommand)]
enum InstrumentCommand {
    Add(AddInstrumentArgs),
}

#[derive(Debug, Args)]
struct AddInstrumentArgs {
    #[arg(long)]
    instrument_id: String,
    #[arg(long)]
    symbol: String,
    #[arg(long, default_value = "spot")]
    instrument_type: String,
    #[arg(long)]
    name: Option<String>,
    #[arg(long)]
    underlying_instrument_id: Option<String>,
    #[arg(long)]
    expiry_unix_nanos: Option<u64>,
    #[arg(long)]
    strike: Option<String>,
    #[arg(long)]
    option_right: Option<String>,
    #[arg(long, default_value = "active")]
    status: String,
}

#[derive(Debug, Subcommand)]
enum ListingCommand {
    Add(AddListingArgs),
}

#[derive(Debug, Args)]
struct AddListingArgs {
    #[arg(long)]
    listing_id: String,
    #[arg(long)]
    instrument_id: String,
    #[arg(long)]
    exchange_id: String,
    #[arg(long)]
    exchange_symbol: String,
    #[arg(long, default_value = "active")]
    status: String,
    #[arg(long, default_value_t = 0)]
    effective_from_unix_nanos: u64,
    #[arg(long)]
    effective_to_unix_nanos: Option<u64>,
}

#[derive(Debug, Args)]
struct AddAssetArgs {
    #[arg(long)]
    asset_id: String,
    #[arg(long)]
    code: String,
    #[arg(long, default_value = "fiat")]
    asset_class: String,
    #[arg(long)]
    name: Option<String>,
    #[arg(long, default_value = "active")]
    status: String,
}

#[derive(Debug, Args)]
struct AssetListArgs {
    #[arg(long)]
    query: Option<String>,
    #[arg(long)]
    status: Option<String>,
    #[arg(long)]
    active_only: bool,
    #[arg(long)]
    limit: usize,
}

#[derive(Debug, Subcommand)]
enum CatalogCommand {
    Exchanges(CatalogListArgs),
    Assets(CatalogListArgs),
    Instruments(CatalogListArgs),
    Listings(CatalogListArgs),
    Markets(MarketQueryArgs),
    Show { identifier: String },
}

#[derive(Debug, Args)]
struct CatalogListArgs {
    #[arg(long)]
    query: Option<String>,
    #[arg(long)]
    status: Option<String>,
    #[arg(long)]
    active_only: bool,
    #[arg(long, default_value_t = 256)]
    limit: usize,
}

#[derive(Debug, Subcommand)]
enum MarketCommand {
    List(MarketQueryArgs),
    Browse(MarketQueryArgs),
    Resolve(MarketQueryArgs),
}

#[derive(Debug, Args)]
struct MarketQueryArgs {
    #[arg(long)]
    symbol: Option<String>,
    #[arg(long)]
    market_id: Option<String>,
    #[arg(long)]
    exchange_id: Option<String>,
    #[arg(long, visible_alias = "exchange")]
    exchange: Option<String>,
    #[arg(long)]
    instrument_kind: Option<String>,
    #[arg(long)]
    asset_type: Option<String>,
    #[arg(long, visible_alias = "market")]
    market: Option<String>,
    #[arg(long)]
    status: Option<String>,
    #[arg(long)]
    limit: Option<usize>,
    #[arg(long)]
    active_only: bool,
}

#[derive(Debug, Args)]
struct OptionChainArgs {
    #[arg(long, visible_alias = "underlying")]
    underlying_instrument_id: String,
    #[arg(long)]
    expiry_unix_nanos: Option<u64>,
    #[arg(long)]
    expiry_from_unix_nanos: Option<u64>,
    #[arg(long)]
    expiry_to_unix_nanos: Option<u64>,
    #[arg(long)]
    option_right: Option<String>,
    #[arg(long, default_value_t = true, action = clap::ArgAction::Set)]
    active_only: bool,
    #[arg(long)]
    limit: Option<usize>,
}

#[derive(Debug, Args)]
struct QueryArgs {
    #[arg(long)]
    text: Option<String>,
    #[arg(long, default_value = "all")]
    kind: String,
    #[arg(long)]
    exchange_id: Option<String>,
    #[arg(long)]
    instrument_kind: Option<String>,
    #[arg(long, visible_alias = "underlying")]
    underlying_instrument_id: Option<String>,
    #[arg(long)]
    status: Option<String>,
    #[arg(long)]
    active_only: bool,
    #[arg(long)]
    as_of_unix_nanos: Option<u64>,
    #[arg(long)]
    limit: Option<usize>,
}

#[derive(Debug, Args)]
struct SearchArgs {
    text: String,
    #[arg(long, default_value_t = 50)]
    limit: usize,
}

impl QueryArgs {
    fn kind(&self) -> ReferenceKind {
        match self.kind.to_ascii_lowercase().as_str() {
            "exchange" => ReferenceKind::Exchange,
            "asset" => ReferenceKind::Asset,
            "instrument" => ReferenceKind::Instrument,
            "listing" => ReferenceKind::Listing,
            "market" => ReferenceKind::Market,
            _ => ReferenceKind::All,
        }
    }

    fn try_into_query(self) -> Result<ReferenceQuery, DomainTypeError> {
        Ok(ReferenceQuery {
            text: self.text,
            exchange_id: self.exchange_id.map(ExchangeId::new).transpose()?,
            instrument_kind: self.instrument_kind.as_deref().map(|value| {
                value
                    .parse()
                    .unwrap_or(kairos_primitives::reference::InstrumentKind::Unknown)
            }),
            underlying_instrument_id: self.underlying_instrument_id,
            status: self.status,
            active_only: self.active_only,
            as_of_unix_nanos: self.as_of_unix_nanos.map(Into::into),
            limit: self.limit,
            ..ReferenceQuery::default()
        })
    }
}

#[cfg(test)]
mod tests {
    use clap::Parser;
    use kairos_reference::application::ConnectedReferenceApplication;
    use kairos_reference_contract::{
        MassiveReferenceSource, ReferenceSourceBinding, ReferenceSourceSyncPolicy,
    };
    use kairos_workspace::JsonRpcControlClient;
    use serde_json::json;

    use super::{
        AddAssetArgs, AddInstrumentArgs, AddListingArgs, CatalogCommand, Cli, Command,
        ConnectedCommand, CoverageCommand, ProviderCommand, ProvidersArgs,
        ReferenceSourceDesiredState, ReferenceSourceScopeKind, StandaloneCommand,
        connected_requires_runtime_control, parse_source_binding, summarize_log_entry,
        upsert_asset_request, upsert_instrument_request, upsert_listing_request,
    };

    fn connected(cli: Cli) -> ConnectedCommand {
        match cli.command {
            Command::Connected(command) => command,
            Command::Standalone(_) => panic!("expected connected command"),
        }
    }

    fn standalone(cli: Cli) -> StandaloneCommand {
        match cli.command {
            Command::Standalone(command) => command,
            Command::Connected(_) => panic!("expected standalone command"),
        }
    }

    #[test]
    fn query_rejects_invalid_exchange_id_without_panicking() {
        let cli = Cli::parse_from([
            "kairos-reference-cli",
            "--workspace",
            ".kairos",
            "standalone",
            "query",
            "--exchange-id",
            " invalid ",
        ]);
        let StandaloneCommand::Query(args) = standalone(cli) else {
            panic!("expected query command");
        };

        let error = args.try_into_query().unwrap_err();

        assert_eq!(
            error.to_string(),
            "ExchangeId cannot contain leading or trailing whitespace"
        );
    }

    #[test]
    fn refresh_accepts_source() {
        let cli = Cli::parse_from([
            "kairos-reference-cli",
            "--workspace",
            ".kairos",
            "connected",
            "refresh",
            "--source",
            "massive-options",
        ]);

        let ConnectedCommand::Refresh(args) = connected(cli) else {
            panic!("expected refresh command");
        };
        assert_eq!(args.source.as_deref(), Some("massive-options"));
    }

    #[test]
    fn sync_normalizes_to_refresh() {
        let cli = Cli::parse_from([
            "kairos-reference-cli",
            "--workspace",
            ".kairos",
            "connected",
            "sync",
            "--source",
            "massive-equity",
        ]);

        let ConnectedCommand::Sync(args) = connected(cli) else {
            panic!("expected sync command");
        };
        assert_eq!(args.source.as_deref(), Some("massive-equity"));
    }

    #[test]
    fn logs_accepts_filters() {
        let cli = Cli::parse_from([
            "kairos-reference-cli",
            "--workspace",
            ".kairos",
            "connected",
            "logs",
            "--follow",
            "--errors",
            "--source",
            "massive-options",
            "--tick",
            "latest",
            "--limit",
            "25",
        ]);

        let ConnectedCommand::Logs(args) = connected(cli) else {
            panic!("expected logs command");
        };
        assert!(args.follow);
        assert!(args.errors);
        assert_eq!(args.source.as_deref(), Some("massive-options"));
        assert_eq!(args.tick.as_deref(), Some("latest"));
        assert_eq!(args.limit, 25);
    }

    #[test]
    fn log_summary_includes_tick_id() {
        let summary = summarize_log_entry(&json!({
            "level": "INFO",
            "event": "app.tick.completed",
            "component": "reference",
            "area": "app",
            "action": "tick",
            "outcome": "completed",
            "tick_id": "reference:run:1:tick:00000000000000000001",
            "source_id": "massive-options",
            "message": "reference refresh completed",
        }));

        assert_eq!(summary["tick"], "reference:run:1:tick:00000000000000000001");
        assert_eq!(summary["event"], "app.tick.completed");
        assert_eq!(summary["source"], "massive-options");
    }

    #[test]
    fn status_accepts_catalog_diagnostic_mode() {
        let cli = Cli::parse_from([
            "kairos-reference-cli",
            "--workspace",
            ".kairos",
            "connected",
            "status",
            "--catalog",
        ]);

        let ConnectedCommand::Status(args) = connected(cli) else {
            panic!("expected status command");
        };
        assert!(args.catalog);
    }

    #[test]
    fn option_chain_is_a_standalone_catalog_command() {
        let cli = Cli::parse_from([
            "kairos-reference-cli",
            "--workspace",
            ".kairos",
            "standalone",
            "option-chain",
            "--underlying",
            "instrument:equity:US:SPY:common",
            "--option-right",
            "call",
            "--limit",
            "25",
        ]);

        let StandaloneCommand::OptionChain(args) = standalone(cli) else {
            panic!("expected standalone option-chain command");
        };
        assert_eq!(
            args.underlying_instrument_id,
            "instrument:equity:US:SPY:common"
        );
        assert_eq!(args.option_right.as_deref(), Some("call"));
        assert_eq!(args.limit, Some(25));
    }

    #[test]
    fn providers_accepts_source_filter() {
        let cli = Cli::parse_from([
            "kairos-reference-cli",
            "--workspace",
            ".kairos",
            "connected",
            "providers",
            "--source",
            "massive-options",
        ]);

        let ConnectedCommand::Providers(args) = connected(cli) else {
            panic!("expected providers command");
        };
        assert_eq!(args.source.as_deref(), Some("massive-options"));
    }

    #[test]
    fn providers_show_accepts_source_id() {
        let cli = Cli::parse_from([
            "kairos-reference-cli",
            "--workspace",
            ".kairos",
            "connected",
            "providers",
            "show",
            "massive-options",
        ]);

        let ConnectedCommand::Providers(args) = connected(cli) else {
            panic!("expected providers command");
        };
        assert_eq!(args.source.as_deref(), None);
        assert!(matches!(
            args.command,
            Some(ProviderCommand::Show { ref source_id }) if source_id == "massive-options"
        ));
    }

    #[test]
    fn providers_pause_and_resume_are_runtime_control_commands() {
        let pause = Cli::parse_from([
            "kairos-reference-cli",
            "--workspace",
            ".kairos",
            "connected",
            "providers",
            "pause",
            "massive-options",
        ]);
        let resume = Cli::parse_from([
            "kairos-reference-cli",
            "--workspace",
            ".kairos",
            "connected",
            "providers",
            "resume",
            "massive-options",
        ]);

        let pause = connected(pause);
        let resume = connected(resume);

        assert!(matches!(
            &pause,
            ConnectedCommand::Providers(ProvidersArgs {
                command: Some(ProviderCommand::Pause { .. }),
                ..
            })
        ));
        assert!(connected_requires_runtime_control(&pause));
        assert!(matches!(
            &resume,
            ConnectedCommand::Providers(ProvidersArgs {
                command: Some(ProviderCommand::Resume { .. }),
                ..
            })
        ));
        assert!(connected_requires_runtime_control(&resume));

        let disable = Cli::parse_from([
            "kairos-reference-cli",
            "--workspace",
            ".kairos",
            "connected",
            "providers",
            "disable",
            "massive-options",
        ]);
        let enable = Cli::parse_from([
            "kairos-reference-cli",
            "--workspace",
            ".kairos",
            "connected",
            "providers",
            "enable",
            "massive-options",
        ]);

        let disable = connected(disable);
        let enable = connected(enable);

        assert!(matches!(
            &disable,
            ConnectedCommand::Providers(ProvidersArgs {
                command: Some(ProviderCommand::Disable { .. }),
                ..
            })
        ));
        assert!(connected_requires_runtime_control(&disable));
        assert!(matches!(
            &enable,
            ConnectedCommand::Providers(ProvidersArgs {
                command: Some(ProviderCommand::Enable { .. }),
                ..
            })
        ));
        assert!(connected_requires_runtime_control(&enable));
    }

    #[test]
    fn providers_add_builds_source_definition_request() {
        let cli = Cli::parse_from([
            "kairos-reference-cli",
            "--workspace",
            ".kairos",
            "connected",
            "providers",
            "add",
            "massive",
            "options",
            "--scope-kind",
            "underlying_instrument",
            "--scope-id",
            "instrument:equity:US:SPY:common",
            "--credential-binding",
            "massive.default",
        ]);

        let command = connected(cli);
        assert!(connected_requires_runtime_control(&command));
        let ConnectedCommand::Providers(args) = command else {
            panic!("expected providers command");
        };
        let request = args.source_definition_request().unwrap();
        assert_eq!(
            request.binding,
            ReferenceSourceBinding::Massive(MassiveReferenceSource::Options)
        );
        assert!(parse_source_binding("massive-options", "options").is_err());
        assert_eq!(
            request.scope.kind,
            ReferenceSourceScopeKind::UnderlyingInstrument
        );
        assert_eq!(
            request.scope.id.as_deref(),
            Some("instrument:equity:US:SPY:common")
        );
        assert_eq!(
            request.credential_binding.as_deref(),
            Some("massive.default")
        );
    }

    #[test]
    fn coverage_list_is_read_only_and_add_remove_are_runtime_control() {
        let list = Cli::parse_from([
            "kairos-reference-cli",
            "--workspace",
            ".kairos",
            "connected",
            "coverage",
        ]);
        let add = Cli::parse_from([
            "kairos-reference-cli",
            "--workspace",
            ".kairos",
            "connected",
            "coverage",
            "add",
            "instrument:equity:US:SPY:common",
        ]);
        let remove = Cli::parse_from([
            "kairos-reference-cli",
            "--workspace",
            ".kairos",
            "connected",
            "coverage",
            "remove",
            "instrument:equity:US:SPY:common",
        ]);

        let list = connected(list);
        let add = connected(add);
        let remove = connected(remove);

        assert!(matches!(
            &list,
            ConnectedCommand::Coverage(super::CoverageArgs { command: None })
        ));
        assert!(!connected_requires_runtime_control(&list));
        assert!(matches!(
            &add,
            ConnectedCommand::Coverage(super::CoverageArgs {
                command: Some(CoverageCommand::Add { .. })
            })
        ));
        assert!(connected_requires_runtime_control(&add));
        assert!(matches!(
            &remove,
            ConnectedCommand::Coverage(super::CoverageArgs {
                command: Some(CoverageCommand::Remove { .. })
            })
        ));
        assert!(connected_requires_runtime_control(&remove));
    }

    #[test]
    fn doctor_is_a_runtime_command() {
        let cli = Cli::parse_from([
            "kairos-reference-cli",
            "--workspace",
            ".kairos",
            "connected",
            "doctor",
        ]);

        assert!(matches!(connected(cli), ConnectedCommand::Doctor));
    }

    #[test]
    fn providers_summary_filters_runtime_sources() {
        let status = serde_json::from_value(json!({
            "status": "degraded",
            "app_runtime": {
                "phase": "serving",
                "actor_id": "reference",
                "source_id": "reference-default",
                "refresh_interval_millis": 300000
            },
            "catalog": {
                "readiness": "degraded",
                "generation": 27,
                "event_sequence": 148995,
                "market_count": 27689
            },
            "sources": [
                {
                    "source_id": "massive-equity",
                    "provider_id": "massive",
                    "desired_state": "enabled",
                    "sync_policy": "paged_snapshot",
                    "phase": "ready",
                    "progress": {"kind": "complete", "pages_done": 1, "pages_total": 1, "records_seen": 10, "records_changed": 2},
                    "last_attempt_unix_nanos": null,
                    "last_success_unix_nanos": null,
                    "consecutive_failures": 0,
                    "stale": false,
                    "has_last_known_good": true
                },
                {
                    "source_id": "massive-options",
                    "provider_id": "massive",
                    "desired_state": "enabled",
                    "sync_policy": "scoped_snapshot",
                    "phase": "syncing",
                    "progress": {"kind": "paged", "pages_done": 3, "pages_total": 8, "records_seen": 300, "records_changed": 12},
                    "work_item": {
                        "work_item_id": "massive-options:SPY",
                        "scope_id": "instrument:equity:US:SPY:common",
                        "scope_kind": "underlying_instrument",
                        "cursor_present": true
                    },
                    "last_attempt_unix_nanos": null,
                    "last_success_unix_nanos": null,
                    "retry_backoff_seconds": 10,
                    "consecutive_failures": 1,
                    "stale": true,
                    "has_last_known_good": true
                }
            ],
            "publication": {"pending_publication_count": 0},
            "diagnostics": []
        }))
        .unwrap();

        let args = ProvidersArgs {
            source: Some("massive-options".to_owned()),
            command: None,
        };
        let value = ConnectedReferenceApplication::<JsonRpcControlClient>::summarize_providers(
            status,
            args.source_filter().unwrap(),
            args.is_show(),
        )
        .unwrap();

        let kairos_reference::application::ReferenceProvidersResult::Many(values) = value else {
            panic!("provider list must return many result");
        };
        assert_eq!(values.len(), 1);
        let value = &values[0];
        assert_eq!(value.source_id, "massive-options");
        assert_eq!(
            value.provider_id.as_ref().map(|id| id.as_str()),
            Some("massive")
        );
        assert_eq!(
            value.desired_state,
            Some(ReferenceSourceDesiredState::Enabled)
        );
        assert_eq!(
            value.sync_policy,
            Some(ReferenceSourceSyncPolicy::ScopedSnapshot)
        );
        assert_eq!(value.phase, "syncing");
        assert_eq!(value.progress, "paged");
        assert_eq!(value.pages_done, Some(3));
        assert_eq!(value.pages_total, Some(8));
        assert_eq!(value.records_seen, Some(300));
        assert_eq!(value.records_changed, Some(12));
        assert_eq!(value.retry_backoff_seconds, Some(10));
        assert_eq!(value.work_item_id.as_deref(), Some("massive-options:SPY"));
        assert_eq!(
            value.scope_id.as_deref(),
            Some("instrument:equity:US:SPY:common")
        );
        assert_eq!(value.scope_kind.as_deref(), Some("underlying_instrument"));
        assert_eq!(value.cursor_present, Some(true));
    }

    #[test]
    fn providers_show_returns_one_runtime_source() {
        let status = serde_json::from_value(json!({
            "status": "ready",
            "app_runtime": {
                "phase": "serving",
                "actor_id": "reference",
                "source_id": "reference-default",
                "refresh_interval_millis": 300000
            },
            "catalog": {
                "readiness": "ready",
                "generation": 27,
                "event_sequence": 148995,
                "market_count": 27689
            },
            "sources": [
                {
                    "source_id": "massive-equity",
                    "provider_id": "massive",
                    "desired_state": "enabled",
                    "sync_policy": "paged_snapshot",
                    "phase": "ready",
                    "progress": {"kind": "complete", "pages_done": 1, "pages_total": 1, "records_seen": 10, "records_changed": 2},
                    "last_attempt_unix_nanos": null,
                    "last_success_unix_nanos": null,
                    "consecutive_failures": 0,
                    "stale": false,
                    "has_last_known_good": true
                },
                {
                    "source_id": "massive-options",
                    "provider_id": "massive",
                    "desired_state": "enabled",
                    "sync_policy": "scoped_snapshot",
                    "phase": "syncing",
                    "progress": {"kind": "paged", "pages_done": 3, "pages_total": 8, "records_seen": 300, "records_changed": 12},
                    "last_attempt_unix_nanos": null,
                    "last_success_unix_nanos": null,
                    "retry_backoff_seconds": 10,
                    "consecutive_failures": 1,
                    "stale": true,
                    "has_last_known_good": true
                }
            ],
            "publication": {"pending_publication_count": 0},
            "diagnostics": []
        }))
        .unwrap();

        let args = ProvidersArgs {
            source: None,
            command: Some(ProviderCommand::Show {
                source_id: "massive-options".to_owned(),
            }),
        };
        let value = ConnectedReferenceApplication::<JsonRpcControlClient>::summarize_providers(
            status,
            args.source_filter().unwrap(),
            args.is_show(),
        )
        .unwrap();

        let kairos_reference::application::ReferenceProvidersResult::One(value) = value else {
            panic!("provider show must return one result");
        };
        assert_eq!(value.source_id, "massive-options");
        assert_eq!(
            value.provider_id.as_ref().map(|id| id.as_str()),
            Some("massive")
        );
        assert_eq!(
            value.desired_state,
            Some(ReferenceSourceDesiredState::Enabled)
        );
        assert_eq!(
            value.sync_policy,
            Some(ReferenceSourceSyncPolicy::ScopedSnapshot)
        );
        assert_eq!(value.phase, "syncing");
        assert_eq!(value.pages_done, Some(3));
        assert_eq!(value.pages_total, Some(8));
        assert_eq!(value.records_seen, Some(300));
        assert_eq!(value.records_changed, Some(12));
        assert_eq!(value.retry_backoff_seconds, Some(10));
    }

    #[test]
    fn coverage_summary_uses_runtime_status_underlyings() {
        let status = serde_json::from_value(json!({
            "status": "ready",
            "app_runtime": {
                "phase": "serving",
                "actor_id": "reference",
                "source_id": "reference-default",
                "refresh_interval_millis": 300000
            },
            "catalog": {
                "readiness": "ready",
                "generation": 27,
                "event_sequence": 148995,
                "market_count": 27689
            },
            "sources": [],
            "coverage": {
                "option_underlyings": ["instrument:equity:US:SPY:common"]
            },
            "publication": {"pending_publication_count": 0},
            "diagnostics": []
        }))
        .unwrap();

        let value =
            ConnectedReferenceApplication::<JsonRpcControlClient>::summarize_coverage(status);

        assert_eq!(value.kind, "massive_options");
        assert_eq!(
            value.option_underlyings[0].as_str(),
            "instrument:equity:US:SPY:common"
        );
    }

    #[test]
    fn providers_show_rejects_ambiguous_source_filter() {
        let args = ProvidersArgs {
            source: Some("massive-equity".to_owned()),
            command: Some(ProviderCommand::Show {
                source_id: "massive-options".to_owned(),
            }),
        };

        let error = args.source_filter().unwrap_err().to_string();

        assert!(error.contains("use either"));
    }

    #[test]
    fn providers_pause_rejects_ambiguous_source_filter() {
        let args = ProvidersArgs {
            source: Some("massive-equity".to_owned()),
            command: Some(ProviderCommand::Pause {
                source_id: "massive-options".to_owned(),
            }),
        };

        let error = args.control_source_id().unwrap_err().to_string();

        assert!(error.contains("cannot be combined"));
    }

    #[test]
    fn doctor_summary_uses_runtime_diagnostics() {
        let status = serde_json::from_value(json!({
            "status": "degraded",
            "app_runtime": {
                "phase": "serving",
                "actor_id": "reference",
                "source_id": "reference-default",
                "refresh_interval_millis": 300000
            },
            "catalog": {
                "readiness": "degraded",
                "generation": 27,
                "event_sequence": 148995,
                "market_count": 27689
            },
            "sources": [
                {
                    "source_id": "massive-options",
                    "phase": "degraded",
                    "progress": {"kind": "unknown", "pages_done": null},
                    "last_attempt_unix_nanos": null,
                    "last_success_unix_nanos": null,
                    "retry_backoff_seconds": 20,
                    "consecutive_failures": 2,
                    "stale": true,
                    "has_last_known_good": true
                }
            ],
            "publication": {"pending_publication_count": 5},
            "diagnostics": [
                {
                    "severity": "warn",
                    "code": "reference.source.degraded",
                    "message": "source is degraded",
                    "next_action": "inspect logs",
                    "source_id": "massive-options"
                }
            ]
        }))
        .unwrap();

        let value = ConnectedReferenceApplication::<JsonRpcControlClient>::summarize_doctor(status)
            .unwrap();

        assert_eq!(value.status, "degraded");
        assert_eq!(value.catalog_readiness, "degraded");
        assert_eq!(value.degraded_source_count, 1);
        assert_eq!(value.pending_publication_count, 5);
        assert_eq!(value.diagnostics[0].code, "reference.source.degraded");
    }

    #[test]
    fn refresh_sync_and_publish_are_runtime_control_commands() {
        let refresh = Cli::parse_from([
            "kairos-reference-cli",
            "--workspace",
            ".kairos",
            "connected",
            "refresh",
            "--source",
            "massive-options",
        ]);
        let sync = Cli::parse_from([
            "kairos-reference-cli",
            "--workspace",
            ".kairos",
            "connected",
            "sync",
        ]);
        let publish = Cli::parse_from([
            "kairos-reference-cli",
            "--workspace",
            ".kairos",
            "connected",
            "publish",
        ]);

        assert!(connected_requires_runtime_control(&connected(refresh)));
        assert!(connected_requires_runtime_control(&connected(sync)));
        assert!(connected_requires_runtime_control(&connected(publish)));

        let connected_refresh = Cli::parse_from([
            "kairos-reference-cli",
            "--workspace",
            ".kairos",
            "connected",
            "refresh",
            "--source",
            "massive-options",
        ]);
        assert!(connected_requires_runtime_control(&connected(
            connected_refresh
        )));
    }

    #[test]
    fn explicit_mode_groups_parse() {
        let standalone_query = Cli::try_parse_from([
            "kairos-reference-cli",
            "--workspace",
            ".kairos",
            "standalone",
            "query",
            "--kind",
            "market",
            "--limit",
            "5",
        ]);
        assert!(
            standalone_query.is_ok(),
            "reference standalone command surface must parse: {standalone_query:?}"
        );

        let connected_status = Cli::try_parse_from([
            "kairos-reference-cli",
            "--workspace",
            ".kairos",
            "connected",
            "status",
        ]);
        assert!(
            connected_status.is_ok(),
            "reference connected command surface must parse: {connected_status:?}"
        );
    }

    #[test]
    fn catalog_group_exposes_read_only_browsing_commands() {
        let assets = Cli::parse_from([
            "kairos-reference-cli",
            "--workspace",
            ".kairos",
            "standalone",
            "catalog",
            "assets",
            "--query",
            "USD",
        ]);
        let markets = Cli::parse_from([
            "kairos-reference-cli",
            "--workspace",
            ".kairos",
            "standalone",
            "catalog",
            "markets",
            "--symbol",
            "SPY",
        ]);
        let show = Cli::parse_from([
            "kairos-reference-cli",
            "--workspace",
            ".kairos",
            "standalone",
            "catalog",
            "show",
            "market:equity:US:SPY",
        ]);

        let assets = standalone(assets);
        let markets = standalone(markets);
        let show = standalone(show);

        assert!(matches!(
            &assets,
            StandaloneCommand::Catalog {
                command: CatalogCommand::Assets(_)
            }
        ));
        assert!(matches!(
            &markets,
            StandaloneCommand::Catalog {
                command: CatalogCommand::Markets(_)
            }
        ));
        assert!(matches!(
            &show,
            StandaloneCommand::Catalog {
                command: CatalogCommand::Show { .. }
            }
        ));
    }

    #[test]
    fn administrative_adds_are_runtime_control_commands() {
        let asset = Cli::parse_from([
            "kairos-reference-cli",
            "--workspace",
            ".kairos",
            "connected",
            "assets",
            "add",
            "--asset-id",
            "asset:usd",
            "--code",
            "USD",
        ]);
        let instrument = Cli::parse_from([
            "kairos-reference-cli",
            "--workspace",
            ".kairos",
            "connected",
            "instruments",
            "add",
            "--instrument-id",
            "instrument:cash:USD",
            "--symbol",
            "USD",
        ]);
        let listing = Cli::parse_from([
            "kairos-reference-cli",
            "--workspace",
            ".kairos",
            "connected",
            "listings",
            "add",
            "--listing-id",
            "listing:usd:cash",
            "--instrument-id",
            "instrument:cash:USD",
            "--exchange-id",
            "exchange:test",
            "--exchange-symbol",
            "USD",
        ]);

        assert!(connected_requires_runtime_control(&connected(asset)));
        assert!(connected_requires_runtime_control(&connected(instrument)));
        assert!(connected_requires_runtime_control(&connected(listing)));
    }

    #[test]
    fn administrative_add_args_map_to_contract_requests() {
        let asset = upsert_asset_request(AddAssetArgs {
            asset_id: "asset:usd".to_owned(),
            code: "USD".to_owned(),
            asset_class: "fiat".to_owned(),
            name: Some("US Dollar".to_owned()),
            status: "active".to_owned(),
        })
        .unwrap();
        assert_eq!(
            serde_json::to_value(asset).unwrap()["asset_id"],
            "asset:usd"
        );

        let instrument = upsert_instrument_request(AddInstrumentArgs {
            instrument_id: "instrument:cash:USD".to_owned(),
            symbol: "USD".to_owned(),
            instrument_type: "spot".to_owned(),
            name: None,
            underlying_instrument_id: None,
            expiry_unix_nanos: None,
            strike: None,
            option_right: None,
            status: "active".to_owned(),
        })
        .unwrap();
        assert_eq!(
            serde_json::to_value(instrument).unwrap()["instrument_id"],
            "instrument:cash:USD"
        );

        let listing = upsert_listing_request(AddListingArgs {
            listing_id: "listing:usd:cash".to_owned(),
            instrument_id: "instrument:cash:USD".to_owned(),
            exchange_id: "exchange:test".to_owned(),
            exchange_symbol: "USD".to_owned(),
            status: "active".to_owned(),
            effective_from_unix_nanos: 0,
            effective_to_unix_nanos: None,
        })
        .unwrap();
        assert_eq!(
            serde_json::to_value(listing).unwrap()["listing_id"],
            "listing:usd:cash"
        );
    }

    #[test]
    fn events_sync_is_not_a_plain_cli_command() {
        assert!(
            Cli::try_parse_from([
                "kairos-reference-cli",
                "--workspace",
                ".kairos",
                "connected",
                "events",
                "sync",
                "--ticker",
                "SPY",
            ])
            .is_err()
        );
    }

    #[test]
    fn defer_publication_is_not_a_plain_cli_option() {
        assert!(
            Cli::try_parse_from([
                "kairos-reference-cli",
                "--workspace",
                ".kairos",
                "--defer-publication",
                "connected",
                "refresh",
            ])
            .is_err()
        );
    }

    #[test]
    fn aeron_transport_is_not_a_plain_cli_option() {
        assert!(
            Cli::try_parse_from([
                "kairos-reference-cli",
                "--workspace",
                ".kairos",
                "--aeron-channel",
                "aeron:ipc",
                "connected",
                "status",
            ])
            .is_err()
        );
    }
}
