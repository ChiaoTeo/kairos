use std::path::PathBuf;
use std::str::FromStr;

use clap::{Args as ClapArgs, Parser, Subcommand, ValueEnum};
use kairos_capital::{
    CapitalCliRequestKind, CapitalStandaloneOutput, CliCapitalApplication,
    ConnectedCapitalApplication, ConnectedCapitalOutput,
};
use kairos_capital_contract::{
    CancelFundingObjectiveRequest, CapitalClient, CapitalConnection, ObserveCapitalDemandRequest,
    PublishFundingObjectiveRequest, QueryCapitalAvailabilityRequest, ReconcileCapitalPlanRequest,
};
use kairos_workspace::Workspace;
use kairos_workspace::cli::{OutputFormat, render};
use serde::Serialize;

#[derive(Debug, Serialize)]
#[serde(untagged)]
enum CapitalCliOutput {
    Standalone(CapitalStandaloneOutput),
    Connected(ConnectedCapitalOutput),
}

#[derive(Debug, Parser)]
#[command(name = "kairos-capital-cli", about = "One-shot Capital commands")]
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
    Plan(PlanCommand),
}

#[derive(Debug, Subcommand)]
enum ConnectedCommand {
    Health(ConnectedTargetArgs),
    Current(ConnectedCurrentCommand),
    Objectives(ConnectedCurrentCommand),
    Demands(ConnectedCurrentCommand),
    Availabilities(ConnectedCurrentCommand),
    Availability(ConnectedRequestFileCommand),
    Routes(ConnectedCurrentCommand),
    Plans(ConnectedCurrentCommand),
    Reservations(ConnectedCurrentCommand),
    Operations(ConnectedCurrentCommand),
    Alerts(ConnectedCurrentCommand),
    #[command(name = "publish-funding-objective")]
    PublishFundingObjective(ConnectedRequestFileCommand),
    #[command(name = "observe-demand")]
    ObserveDemand(ConnectedRequestFileCommand),
    #[command(name = "cancel-funding-objective")]
    CancelFundingObjective(ConnectedRequestFileCommand),
    #[command(name = "reconcile-plan")]
    ReconcilePlan(ConnectedRequestFileCommand),
}

#[derive(Debug, ClapArgs)]
struct ConnectedTargetArgs {
    #[arg(long)]
    socket: Option<PathBuf>,
    #[arg(long)]
    view_root: Option<PathBuf>,
}

#[derive(Debug, ClapArgs)]
struct ConnectedCurrentCommand {
    #[command(flatten)]
    target: ConnectedTargetArgs,
    #[arg(long)]
    capital_group_id: String,
}

#[derive(Debug, ClapArgs)]
struct ConnectedRequestFileCommand {
    #[command(flatten)]
    target: ConnectedTargetArgs,
    #[arg(long)]
    file: PathBuf,
}

#[derive(Clone, Copy, Debug, ValueEnum)]
enum CapitalRequestKind {
    FundingObjective,
    CapitalDemand,
    Availability,
    CancelFundingObjective,
    ReconcilePlan,
}

impl From<CapitalRequestKind> for CapitalCliRequestKind {
    fn from(value: CapitalRequestKind) -> Self {
        match value {
            CapitalRequestKind::FundingObjective => Self::FundingObjective,
            CapitalRequestKind::CapitalDemand => Self::CapitalDemand,
            CapitalRequestKind::Availability => Self::Availability,
            CapitalRequestKind::CancelFundingObjective => Self::CancelFundingObjective,
            CapitalRequestKind::ReconcilePlan => Self::ReconcilePlan,
        }
    }
}

#[derive(Debug, ClapArgs)]
struct SchemaCommand {
    #[arg(value_enum)]
    kind: Option<CapitalRequestKind>,
}

#[derive(Debug, ClapArgs)]
struct DoctorCommand {
    #[arg(long, value_enum)]
    kind: CapitalRequestKind,
    #[arg(long)]
    file: PathBuf,
}

#[derive(Debug, ClapArgs)]
struct PreviewCommand {
    #[arg(long, value_enum)]
    kind: CapitalRequestKind,
    #[arg(long)]
    file: PathBuf,
}

#[derive(Debug, ClapArgs)]
struct PlanCommand {
    #[arg(long = "objective-file")]
    objective_files: Vec<PathBuf>,
    #[arg(long = "demand-file")]
    demand_files: Vec<PathBuf>,
    #[arg(long = "availability-file")]
    availability_files: Vec<PathBuf>,
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();
    let workspace = Workspace::open(&args.workspace)?;
    let output = args.output.unwrap_or_else(|| {
        OutputFormat::from_workspace(workspace.cli_format())
            .expect("workspace output format validated")
    });
    let value = match args.command {
        Command::Standalone(command) => {
            CapitalCliOutput::Standalone(run_standalone(command, &workspace)?)
        },
        Command::Connected(command) => {
            CapitalCliOutput::Connected(run_connected(command, &workspace).await?)
        },
    };
    println!("{}", render(&value, output));
    Ok(())
}

async fn run_connected(
    command: ConnectedCommand,
    workspace: &Workspace,
) -> Result<ConnectedCapitalOutput, Box<dyn std::error::Error>> {
    match command {
        ConnectedCommand::Health(target) => Ok(ConnectedCapitalOutput::Health(
            connected_capital_app(target, workspace)?.health().await?,
        )),
        ConnectedCommand::Current(command) => {
            let capital_group_id = command.capital_group_id.clone();
            Ok(ConnectedCapitalOutput::Current(
                connected_capital_app(command.target, workspace)?.current(capital_group_id)?,
            ))
        },
        ConnectedCommand::Objectives(command) => {
            let capital_group_id = command.capital_group_id.clone();
            Ok(ConnectedCapitalOutput::Objectives(
                connected_capital_app(command.target, workspace)?.objectives(capital_group_id)?,
            ))
        },
        ConnectedCommand::Demands(command) => {
            let capital_group_id = command.capital_group_id.clone();
            Ok(ConnectedCapitalOutput::Demands(
                connected_capital_app(command.target, workspace)?.demands(capital_group_id)?,
            ))
        },
        ConnectedCommand::Availabilities(command) => {
            let capital_group_id = command.capital_group_id.clone();
            Ok(ConnectedCapitalOutput::Availabilities(
                connected_capital_app(command.target, workspace)?
                    .availabilities(capital_group_id)?,
            ))
        },
        ConnectedCommand::Availability(command) => {
            let application = connected_capital_app(command.target, workspace)?;
            let request: QueryCapitalAvailabilityRequest = read_json_file(&command.file)?;
            Ok(ConnectedCapitalOutput::Availability(
                application.query_capital_availability(request).await?,
            ))
        },
        ConnectedCommand::Routes(command) => {
            let capital_group_id = command.capital_group_id.clone();
            Ok(ConnectedCapitalOutput::Routes(
                connected_capital_app(command.target, workspace)?.routes(capital_group_id)?,
            ))
        },
        ConnectedCommand::Plans(command) => {
            let capital_group_id = command.capital_group_id.clone();
            Ok(ConnectedCapitalOutput::Plans(
                connected_capital_app(command.target, workspace)?.plans(capital_group_id)?,
            ))
        },
        ConnectedCommand::Reservations(command) => {
            let capital_group_id = command.capital_group_id.clone();
            Ok(ConnectedCapitalOutput::Reservations(
                connected_capital_app(command.target, workspace)?.reservations(capital_group_id)?,
            ))
        },
        ConnectedCommand::Operations(command) => {
            let capital_group_id = command.capital_group_id.clone();
            Ok(ConnectedCapitalOutput::Operations(
                connected_capital_app(command.target, workspace)?.operations(capital_group_id)?,
            ))
        },
        ConnectedCommand::Alerts(command) => {
            let capital_group_id = command.capital_group_id.clone();
            Ok(ConnectedCapitalOutput::Alerts(
                connected_capital_app(command.target, workspace)?.alerts(capital_group_id)?,
            ))
        },
        ConnectedCommand::PublishFundingObjective(command) => {
            let application = connected_capital_app(command.target, workspace)?;
            let request: PublishFundingObjectiveRequest = read_json_file(&command.file)?;
            Ok(ConnectedCapitalOutput::Control(
                application.publish_funding_objective(request).await?,
            ))
        },
        ConnectedCommand::ObserveDemand(command) => {
            let application = connected_capital_app(command.target, workspace)?;
            let request: ObserveCapitalDemandRequest = read_json_file(&command.file)?;
            Ok(ConnectedCapitalOutput::Demand(
                application.observe_capital_demand(request).await?,
            ))
        },
        ConnectedCommand::CancelFundingObjective(command) => {
            let application = connected_capital_app(command.target, workspace)?;
            let request: CancelFundingObjectiveRequest = read_json_file(&command.file)?;
            Ok(ConnectedCapitalOutput::Control(
                application.cancel_funding_objective(request).await?,
            ))
        },
        ConnectedCommand::ReconcilePlan(command) => {
            let application = connected_capital_app(command.target, workspace)?;
            let request: ReconcileCapitalPlanRequest = read_json_file(&command.file)?;
            Ok(ConnectedCapitalOutput::Reconcile(
                application.reconcile_capital_plan(request).await?,
            ))
        },
    }
}

fn read_json_file<T: serde::de::DeserializeOwned>(
    path: &std::path::Path,
) -> Result<T, Box<dyn std::error::Error>> {
    let data = std::fs::read_to_string(path)?;
    Ok(serde_json::from_str(&data)?)
}

fn run_standalone(
    command: StandaloneCommand,
    workspace: &Workspace,
) -> Result<CapitalStandaloneOutput, Box<dyn std::error::Error>> {
    let application = CliCapitalApplication::open(workspace);
    match command {
        StandaloneCommand::Schema(command) => Ok(CapitalStandaloneOutput::Schema(
            application.schema(command.kind.map(Into::into)),
        )),
        StandaloneCommand::Doctor(command) => application
            .doctor(command.kind.into(), &command.file)
            .map(CapitalStandaloneOutput::Validation),
        StandaloneCommand::Preview(command) => application
            .preview(command.kind.into(), &command.file)
            .map(CapitalStandaloneOutput::Preview),
        StandaloneCommand::Plan(command) => application
            .plan(
                &command.objective_files,
                &command.demand_files,
                &command.availability_files,
            )
            .map(CapitalStandaloneOutput::Plan),
    }
}

fn connected_capital_app(
    target: ConnectedTargetArgs,
    workspace: &Workspace,
) -> Result<ConnectedCapitalApplication, Box<dyn std::error::Error>> {
    let socket = match target.socket {
        Some(socket) => socket,
        None => workspace.process_socket("capital")?,
    };
    let connection = CapitalConnection::control_only(socket);
    let connection = match target.view_root {
        Some(view_root) => connection.with_view_root(view_root),
        None => connection.with_view_root(workspace.root().join("snapshots")),
    };
    Ok(ConnectedCapitalApplication::connect(
        CapitalClient::connect(connection),
    ))
}
