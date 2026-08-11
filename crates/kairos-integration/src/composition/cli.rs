use crate::application::capabilities::account_facts::{
    ExternalAccountIdentity, ExternalAccountSegment, ExternalDecimal,
};
use crate::application::{
    AsyncEarnConnection, AsyncTransferConnection, CommandOutcome, EarnActionResult, EarnPosition,
    EarnProduct, EarnProductType, EarnRedeemRequest, EarnReward, EarnSubscribeRequest,
    TransferRequest, TransferResult,
};
use crate::participants::binance::{
    BinanceConnection, BinanceConnectionConfig, BinancePrincipalConfig, BinanceQuotaAllocation,
    BinanceSharedQuotaConfig,
};
use clap::{Args, Parser, Subcommand};
use std::path::PathBuf;

#[derive(Debug, Parser)]
#[command(
    name = "kairos-integration-cli",
    about = "Provider integration operations"
)]
struct Cli {
    #[arg(long, global = true, default_value = "json")]
    output: String,
    #[command(flatten)]
    connection: ConnectionArgs,
    #[command(subcommand)]
    command: Command,
}

#[derive(Clone, Debug, Args)]
struct ConnectionArgs {
    #[arg(long, default_value = "binance")]
    provider: String,
    #[arg(long, env = "BINANCE_API_KEY")]
    api_key: String,
    #[arg(long, env = "BINANCE_API_SECRET")]
    secret: String,
    #[arg(long, default_value = "https://api.binance.com")]
    base_url: String,
    #[arg(long, default_value = "live")]
    environment: String,
    #[arg(long, default_value = "integration-cli")]
    binding_id: String,
    #[arg(long, default_value = "integration-cli")]
    principal_id: String,
    #[arg(long, default_value = "default-egress")]
    egress_scope_id: String,
    #[arg(long, default_value = "/tmp/kairos-binance-quota.mmap")]
    quota_ledger: PathBuf,
    #[arg(long, default_value_t = 1_000)]
    request_weight_per_minute: u32,
    #[arg(long, default_value_t = 50)]
    cancel_reserve_weight: u32,
}

#[derive(Debug, Subcommand)]
enum Command {
    Transfer {
        #[arg(long)]
        account_id: String,
        #[arg(long)]
        source_segment: String,
        #[arg(long)]
        destination_segment: String,
        #[arg(long)]
        asset: String,
        #[arg(long)]
        amount_mantissa: i64,
        #[arg(long, default_value_t = 0)]
        amount_scale: u8,
        #[arg(long, default_value = "live")]
        environment: String,
    },
    Earn {
        #[command(subcommand)]
        command: EarnCommand,
    },
}

#[derive(Debug, Subcommand)]
enum EarnCommand {
    Products {
        #[arg(long)]
        asset: Option<String>,
        #[arg(long)]
        product_type: Option<String>,
    },
    Positions {
        #[arg(long)]
        asset: Option<String>,
    },
    Rewards {
        #[arg(long)]
        asset: Option<String>,
    },
    Subscribe {
        #[arg(long)]
        product_id: String,
        #[arg(long, default_value = "flexible")]
        product_type: String,
        #[arg(long)]
        amount: String,
        #[arg(long)]
        auto_renew: Option<bool>,
    },
    Redeem {
        #[arg(long)]
        product_id: String,
        #[arg(long, default_value = "flexible")]
        product_type: String,
        #[arg(long)]
        amount: Option<String>,
        #[arg(long)]
        destination_account: Option<String>,
    },
}

pub async fn run() -> Result<(), Box<dyn std::error::Error>> {
    let cli = Cli::parse();
    if !cli.output.eq_ignore_ascii_case("json") {
        return Err("kairos-integration-cli currently supports only JSON output".into());
    }
    if !cli
        .connection
        .provider
        .trim()
        .eq_ignore_ascii_case("binance")
    {
        return Err(format!(
            "unsupported integration provider: {}",
            cli.connection.provider
        )
        .into());
    }
    let shared = BinanceConnection::connect(BinanceConnectionConfig {
        environment: cli.connection.environment,
        rest_base_url: cli.connection.base_url,
        quota: BinanceQuotaAllocation {
            request_weight_per_minute: cli.connection.request_weight_per_minute,
            cancel_reserve_weight: cli.connection.cancel_reserve_weight,
        },
        shared_quota: Some(BinanceSharedQuotaConfig {
            ledger_path: cli.connection.quota_ledger,
            egress_scope_id: cli.connection.egress_scope_id,
        }),
    })?;
    let principal = shared.principal_connection(BinancePrincipalConfig {
        binding_id: cli.connection.binding_id,
        principal_id: Some(cli.connection.principal_id),
        api_key: cli.connection.api_key.into(),
        secret: cli.connection.secret.into(),
        principal_quota: None,
    })?;
    match cli.command {
        Command::Transfer {
            account_id,
            source_segment,
            destination_segment,
            asset,
            amount_mantissa,
            amount_scale,
            environment,
        } => {
            let mut connection = principal.transfer();
            let identity = ExternalAccountIdentity::new("binance", account_id)?;
            let result = AsyncTransferConnection::transfer(
                &mut connection,
                &TransferRequest {
                    source: ExternalAccountSegment {
                        identity: identity.clone(),
                        segment_key: kairos_domain_types::SegmentKey::new(source_segment)?,
                        environment: environment.clone(),
                        account_model: None,
                    },
                    destination: ExternalAccountSegment {
                        identity,
                        segment_key: kairos_domain_types::SegmentKey::new(destination_segment)?,
                        environment,
                        account_model: None,
                    },
                    asset,
                    amount: ExternalDecimal::new(amount_mantissa, amount_scale),
                },
            )
            .await?;
            print_json(transfer_outcome_json(result))?;
        }
        Command::Earn { command } => {
            let mut connection = principal.earn();
            let value = match command {
                EarnCommand::Products {
                    asset,
                    product_type,
                } => {
                    let products = AsyncEarnConnection::products(
                        &mut connection,
                        asset.as_deref(),
                        product_type
                            .as_deref()
                            .map(parse_product_type)
                            .transpose()?,
                    )
                    .await?;
                    serde_json::json!({
                        "products": products.iter().map(product_json).collect::<Vec<_>>()
                    })
                }
                EarnCommand::Positions { asset } => {
                    serde_json::json!({
                        "positions": AsyncEarnConnection::positions(&mut connection, asset.as_deref())
                            .await?
                            .iter()
                            .map(position_json)
                            .collect::<Vec<_>>()
                    })
                }
                EarnCommand::Rewards { asset } => {
                    serde_json::json!({
                        "rewards": AsyncEarnConnection::rewards(&mut connection, asset.as_deref())
                            .await?
                            .iter()
                            .map(reward_json)
                            .collect::<Vec<_>>()
                    })
                }
                EarnCommand::Subscribe {
                    product_id,
                    product_type,
                    amount,
                    auto_renew,
                } => action_outcome_json(
                    AsyncEarnConnection::subscribe(
                        &mut connection,
                        &EarnSubscribeRequest {
                            product_id,
                            product_type: parse_product_type(&product_type)?,
                            amount: amount.parse::<kairos_domain_types::Quantity>()?,
                            auto_renew,
                        },
                    )
                    .await?,
                ),
                EarnCommand::Redeem {
                    product_id,
                    product_type,
                    amount,
                    destination_account,
                } => action_outcome_json(
                    AsyncEarnConnection::redeem(
                        &mut connection,
                        &EarnRedeemRequest {
                            product_id,
                            product_type: parse_product_type(&product_type)?,
                            amount: amount
                                .map(|value| value.parse::<kairos_domain_types::Quantity>())
                                .transpose()?,
                            destination_account,
                        },
                    )
                    .await?,
                ),
            };
            print_json(value)?;
        }
    }
    Ok(())
}

fn parse_product_type(value: &str) -> Result<EarnProductType, Box<dyn std::error::Error>> {
    match value.trim().to_ascii_lowercase().as_str() {
        "flexible" => Ok(EarnProductType::Flexible),
        "locked" => Ok(EarnProductType::Locked),
        _ => Err(format!("unsupported earn product type: {value}").into()),
    }
}

fn print_json(value: serde_json::Value) -> Result<(), Box<dyn std::error::Error>> {
    println!("{}", serde_json::to_string(&value)?);
    Ok(())
}

fn product_json(value: &EarnProduct) -> serde_json::Value {
    serde_json::json!({
        "product_id": value.product_id,
        "asset": value.asset.to_string(),
        "product_type": format!("{:?}", value.product_type).to_ascii_lowercase(),
        "annual_rate": value.annual_rate.to_string(),
        "min_amount": value.min_amount.to_string(),
        "max_amount": value.max_amount.to_string(),
        "status": value.status,
        "duration_days": value.duration_days,
    })
}

fn position_json(value: &EarnPosition) -> serde_json::Value {
    serde_json::json!({
        "product_id": value.product_id,
        "asset": value.asset.to_string(),
        "amount": value.amount.to_string(),
        "rewards": value.rewards.to_string(),
        "annual_rate": value.annual_rate.to_string(),
        "status": value.status,
        "updated_at_unix_millis": value.updated_at_unix_millis,
    })
}

fn reward_json(value: &EarnReward) -> serde_json::Value {
    serde_json::json!({
        "asset": value.asset.to_string(),
        "amount": value.amount.to_string(),
        "product_id": value.product_id,
        "occurred_at_unix_millis": value.occurred_at_unix_millis,
    })
}

fn action_json(value: EarnActionResult) -> serde_json::Value {
    serde_json::json!({
        "accepted": value.accepted,
        "action_id": value.action_id,
        "status": value.status,
        "reason": value.reason,
    })
}

fn action_outcome_json(outcome: CommandOutcome<EarnActionResult>) -> serde_json::Value {
    match outcome {
        CommandOutcome::Confirmed(value) => serde_json::json!({
            "outcome": "confirmed",
            "result": action_json(value),
        }),
        CommandOutcome::Rejected(rejection) => serde_json::json!({
            "outcome": "rejected",
            "code": rejection.code,
            "message": rejection.message,
            "provider_request_id": rejection.provider_request_id,
        }),
        CommandOutcome::Indeterminate(command) => serde_json::json!({
            "outcome": "indeterminate",
            "delivery_certainty": format!("{:?}", command.certainty).to_ascii_lowercase(),
            "message": command.message,
            "provider_request_id": command.provider_request_id,
        }),
    }
}

fn transfer_outcome_json(outcome: CommandOutcome<TransferResult>) -> serde_json::Value {
    match outcome {
        CommandOutcome::Confirmed(value) => serde_json::json!({
            "outcome": "confirmed",
            "accepted": value.accepted,
            "reference_id": value.reference_id,
            "reason": value.reason,
        }),
        CommandOutcome::Rejected(rejection) => serde_json::json!({
            "outcome": "rejected",
            "code": rejection.code,
            "message": rejection.message,
            "provider_request_id": rejection.provider_request_id,
        }),
        CommandOutcome::Indeterminate(command) => serde_json::json!({
            "outcome": "indeterminate",
            "delivery_certainty": format!("{:?}", command.certainty).to_ascii_lowercase(),
            "message": command.message,
            "provider_request_id": command.provider_request_id,
        }),
    }
}
