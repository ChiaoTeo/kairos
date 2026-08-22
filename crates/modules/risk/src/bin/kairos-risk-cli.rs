use std::path::{Path, PathBuf};
use std::str::FromStr;

use clap::{Args as ClapArgs, Parser, Subcommand, ValueEnum};
use kairos_primitives::account::AccountId;
use kairos_primitives::reference::Exchange;
use kairos_primitives::risk::ReservationId;
use kairos_primitives::runtime::StrategyId;
use kairos_primitives::time::UnixNanos;
use kairos_risk::{CliRiskApplication, ConnectedRiskApplication, RiskCliRequestKind};
use kairos_risk_contract::{
    AdvanceRiskTimeRequest, Amount, AuthorizeRequest, CircuitScope, CloseCircuitRequest,
    ConsumeReservationRequest, OpenCircuitRequest, PublishPolicyRequest, ReleaseReservationRequest,
    ResizeReservationRequest, RiskClient, RiskConnection,
};
use kairos_workspace::Workspace;
use kairos_workspace::cli::{OutputFormat, render};
use serde_json::{Value, json};

/// One-shot Risk inspection commands.
///
/// Runtime control belongs to the System boundary and is performed through
/// the process-owned socket by a typed system client.
#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    let workspace = Workspace::open(&args.workspace)?;
    let output = args.output.unwrap_or_else(|| {
        OutputFormat::from_workspace(workspace.cli_format())
            .expect("workspace output format validated")
    });
    let value = match args.command {
        Command::Standalone(command) => run_standalone(command, &workspace)?,
        Command::Connected(command) => run_connected(command, &workspace).await?,
    };
    println!("{}", render(&value, output));
    Ok(())
}

fn run_standalone(
    command: StandaloneCommand,
    workspace: &Workspace,
) -> Result<Value, Box<dyn std::error::Error>> {
    let application = CliRiskApplication::open(workspace);
    match command {
        StandaloneCommand::Schema(command) => Ok(application.schema(command.kind.map(Into::into))),
        StandaloneCommand::Doctor(command) => {
            application.doctor(command.kind.into(), &command.file)
        },
        StandaloneCommand::Preview(command) => {
            application.preview(&command.policy_file, &command.request_file)
        },
    }
}

async fn run_connected(
    command: ConnectedCommand,
    workspace: &Workspace,
) -> Result<Value, Box<dyn std::error::Error>> {
    match command {
        ConnectedCommand::Health(target) => connected_risk_app(target, workspace)?.health().await,
        ConnectedCommand::Latest(target) => {
            let actor_id = target.actor_id.clone();
            connected_risk_app(target, workspace)?.latest(actor_id)
        },
        ConnectedCommand::Limits(target) => {
            let actor_id = target.actor_id.clone();
            connected_risk_app(target, workspace)?.limits(actor_id)
        },
        ConnectedCommand::Reservations(target) => {
            let actor_id = target.actor_id.clone();
            connected_risk_app(target, workspace)?.reservations(actor_id)
        },
        ConnectedCommand::Circuits(target) => {
            let actor_id = target.actor_id.clone();
            connected_risk_app(target, workspace)?.circuits(actor_id)
        },
        ConnectedCommand::PreTradeCheck(command) => {
            let application = connected_risk_app(command.target, workspace)?;
            let request: AuthorizeRequest = read_json_file(&command.file)?;
            application.pre_trade_check(request).await
        },
        ConnectedCommand::AuthorizeReserve(command) => {
            let application = connected_risk_app(command.target, workspace)?;
            let request: AuthorizeRequest = read_json_file(&command.file)?;
            application.authorize_and_reserve(request).await
        },
        ConnectedCommand::Release(command) => {
            let application = connected_risk_app(command.target, workspace)?;
            let request = ReleaseReservationRequest {
                reservation_id: reservation_id(command.reservation_id)?,
                at_unix_nanos: UnixNanos::new(command.at_unix_nanos),
            };
            application.release_reservation(request).await
        },
        ConnectedCommand::Consume(command) => {
            let application = connected_risk_app(command.target, workspace)?;
            let request = ConsumeReservationRequest {
                reservation_id: reservation_id(command.reservation_id)?,
                at_unix_nanos: UnixNanos::new(command.at_unix_nanos),
            };
            application.consume_reservation(request).await
        },
        ConnectedCommand::Resize(command) => {
            let application = connected_risk_app(command.target, workspace)?;
            let request = ResizeReservationRequest {
                reservation_id: reservation_id(command.reservation_id)?,
                amount: amount(command.amount)?,
                at_unix_nanos: UnixNanos::new(command.at_unix_nanos),
            };
            application.resize_reservation(request).await
        },
        ConnectedCommand::OpenCircuit(command) => {
            let application = connected_risk_app(command.target, workspace)?;
            let request = OpenCircuitRequest {
                scope: circuit_scope(
                    command.scope.account_id,
                    command.scope.strategy_id,
                    command.scope.exchange_id,
                )?,
                at_unix_nanos: UnixNanos::new(command.at_unix_nanos),
                reset_at_unix_nanos: command.reset_at_unix_nanos.map(UnixNanos::new),
                reason: command.reason,
            };
            application.open_circuit(request).await
        },
        ConnectedCommand::CloseCircuit(command) => {
            let application = connected_risk_app(command.target, workspace)?;
            let request = CloseCircuitRequest {
                scope: circuit_scope(
                    command.scope.account_id,
                    command.scope.strategy_id,
                    command.scope.exchange_id,
                )?,
                at_unix_nanos: UnixNanos::new(command.at_unix_nanos),
            };
            application.close_circuit(request).await
        },
        ConnectedCommand::PublishPolicy(command) => {
            let application = connected_risk_app(command.target, workspace)?;
            let request: PublishPolicyRequest = read_json_file(&command.file)?;
            application.publish_policy(request).await
        },
        ConnectedCommand::AdvanceTime(command) => {
            let application = connected_risk_app(command.target, workspace)?;
            let request = AdvanceRiskTimeRequest {
                event_time_unix_nanos: UnixNanos::new(command.event_time_unix_nanos),
            };
            application.advance_time(request).await
        },
    }
}

fn read_json_file<T: serde::de::DeserializeOwned>(
    path: &Path,
) -> Result<T, Box<dyn std::error::Error>> {
    let data = std::fs::read_to_string(path)?;
    Ok(serde_json::from_str(&data)?)
}

fn reservation_id(value: String) -> Result<ReservationId, Box<dyn std::error::Error>> {
    ReservationId::new(value).map_err(|error| error.to_string().into())
}

fn amount(value: String) -> Result<Amount, Box<dyn std::error::Error>> {
    serde_json::from_value(serde_json::Value::String(value)).map_err(Into::into)
}

fn circuit_scope(
    account_id: Option<String>,
    strategy_id: Option<String>,
    exchange_id: Option<String>,
) -> Result<CircuitScope, Box<dyn std::error::Error>> {
    Ok(CircuitScope {
        account_id: account_id.map(AccountId::new).transpose()?,
        strategy_id: strategy_id.map(StrategyId::new).transpose()?,
        exchange_id: exchange_id.map(Exchange::new).transpose()?,
    })
}

fn connected_risk_app(
    target: ConnectedTargetArgs,
    workspace: &Workspace,
) -> Result<ConnectedRiskApplication, Box<dyn std::error::Error>> {
    let socket = match target.socket {
        Some(socket) => socket,
        None => workspace.process_socket("risk")?,
    };
    let connection = RiskConnection::control_only(socket);
    let connection = match target.view_root {
        Some(view_root) => connection.with_view_root(view_root),
        None => connection.with_view_root(workspace.root().join("snapshots")),
    };
    Ok(ConnectedRiskApplication::connect(RiskClient::connect(
        connection,
    )?))
}

#[derive(Debug, Parser)]
#[command(name = "kairos-risk-cli", about = "One-shot Risk commands")]
struct Args {
    #[arg(long)]
    workspace: String,
    #[arg(long, global = true, value_parser = OutputFormat::from_str)]
    output: Option<OutputFormat>,
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    #[command(subcommand)]
    Standalone(StandaloneCommand),
    #[command(subcommand)]
    Connected(ConnectedCommand),
}

#[derive(Debug, Subcommand)]
enum StandaloneCommand {
    Schema(SchemaCommand),
    Doctor(DoctorCommand),
    Preview(PreviewCommand),
}

#[derive(Clone, Copy, Debug, ValueEnum)]
enum RiskRequestKind {
    Authorization,
    Policy,
}

impl From<RiskRequestKind> for RiskCliRequestKind {
    fn from(value: RiskRequestKind) -> Self {
        match value {
            RiskRequestKind::Authorization => Self::Authorization,
            RiskRequestKind::Policy => Self::Policy,
        }
    }
}

#[derive(Debug, ClapArgs)]
struct SchemaCommand {
    #[arg(value_enum)]
    kind: Option<RiskRequestKind>,
}

#[derive(Debug, ClapArgs)]
struct DoctorCommand {
    #[arg(long, value_enum)]
    kind: RiskRequestKind,
    #[arg(long)]
    file: PathBuf,
}

#[derive(Debug, ClapArgs)]
struct PreviewCommand {
    #[arg(long = "policy-file", required = true)]
    policy_file: Vec<PathBuf>,
    #[arg(long)]
    request_file: PathBuf,
}

#[derive(Debug, Subcommand)]
enum ConnectedCommand {
    Health(ConnectedTargetArgs),
    Latest(ConnectedTargetArgs),
    Limits(ConnectedTargetArgs),
    Reservations(ConnectedTargetArgs),
    Circuits(ConnectedTargetArgs),
    PreTradeCheck(AuthorizeFileCommand),
    AuthorizeReserve(AuthorizeFileCommand),
    Release(ReservationMutationCommand),
    Consume(ReservationMutationCommand),
    Resize(ResizeReservationCommand),
    OpenCircuit(OpenCircuitCommand),
    CloseCircuit(CloseCircuitCommand),
    PublishPolicy(PolicyFileCommand),
    AdvanceTime(AdvanceTimeCommand),
}

#[derive(Clone, Debug, ClapArgs)]
struct ConnectedTargetArgs {
    #[arg(long)]
    socket: Option<PathBuf>,
    #[arg(long)]
    view_root: Option<PathBuf>,
    #[arg(long, default_value = "risk")]
    actor_id: String,
}

#[derive(Debug, ClapArgs)]
struct AuthorizeFileCommand {
    #[command(flatten)]
    target: ConnectedTargetArgs,
    #[arg(long)]
    file: PathBuf,
}

#[derive(Debug, ClapArgs)]
struct ReservationMutationCommand {
    #[command(flatten)]
    target: ConnectedTargetArgs,
    #[arg(long)]
    reservation_id: String,
    #[arg(long)]
    at_unix_nanos: u64,
}

#[derive(Debug, ClapArgs)]
struct ResizeReservationCommand {
    #[command(flatten)]
    target: ConnectedTargetArgs,
    #[arg(long)]
    reservation_id: String,
    #[arg(long)]
    amount: String,
    #[arg(long)]
    at_unix_nanos: u64,
}

#[derive(Debug, ClapArgs)]
struct CircuitScopeArgs {
    #[arg(long)]
    account_id: Option<String>,
    #[arg(long)]
    strategy_id: Option<String>,
    #[arg(long)]
    exchange_id: Option<String>,
}

#[derive(Debug, ClapArgs)]
struct OpenCircuitCommand {
    #[command(flatten)]
    target: ConnectedTargetArgs,
    #[command(flatten)]
    scope: CircuitScopeArgs,
    #[arg(long)]
    at_unix_nanos: u64,
    #[arg(long)]
    reset_at_unix_nanos: Option<u64>,
    #[arg(long)]
    reason: String,
}

#[derive(Debug, ClapArgs)]
struct CloseCircuitCommand {
    #[command(flatten)]
    target: ConnectedTargetArgs,
    #[command(flatten)]
    scope: CircuitScopeArgs,
    #[arg(long)]
    at_unix_nanos: u64,
}

#[derive(Debug, ClapArgs)]
struct PolicyFileCommand {
    #[command(flatten)]
    target: ConnectedTargetArgs,
    #[arg(long)]
    file: PathBuf,
}

#[derive(Debug, ClapArgs)]
struct AdvanceTimeCommand {
    #[command(flatten)]
    target: ConnectedTargetArgs,
    #[arg(long)]
    event_time_unix_nanos: u64,
}
