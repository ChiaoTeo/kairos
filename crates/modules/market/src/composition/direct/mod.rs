//! Composition for one short-lived standalone Market provider query.

use std::path::Path;

use kairos_conflux::{
    BinanceCredential, BinanceOptionsRestConnection, BinanceRestConfig, BinanceSpotRestConnection,
    BinanceStocksRestConnection, ConnectionKey, MassiveInstrumentQuery, MassiveRestConfig,
    MassiveRestConnection, load_workspace_credential,
};
use kairos_primitives::market::{ObservationKind, Provider};
use kairos_workspace::Workspace;

use super::config::{
    BinanceDerivativeProduct, MarketConfig, MarketProviderBinding, MassiveMarketProduct,
};
use crate::application::{
    CliMarketApplication, CliMarketOnceProvider, CliMarketOnceRequest, CliMarketRoute,
};
use crate::services::direct::DirectMarketConnection;

pub fn standalone_market_routes(
    workspace_root: Option<&Path>,
    market_type: &str,
    observation_kind: ObservationKind,
) -> Result<Vec<CliMarketRoute>, Box<dyn std::error::Error>> {
    let mut routes = Vec::new();
    if let Some(workspace_root) = workspace_root {
        let workspace = Workspace::open(workspace_root)?;
        let config = MarketConfig::load(&workspace)?;
        for (_, binding) in &config.providers {
            let Some((connection, configured_market_type, capabilities)) = direct_source(binding)
            else {
                continue;
            };
            if binding.enabled()
                && configured_market_type == market_type
                && capabilities.contains(&observation_kind)
            {
                let provider = Provider::new(provider_name(binding).expect("direct provider"))?;
                if routes
                    .iter()
                    .any(|route: &CliMarketRoute| route.provider == provider)
                {
                    continue;
                }
                routes.push(CliMarketRoute {
                    provider,
                    connection,
                    observation_kinds: capabilities,
                });
            }
        }
    }
    if market_type == "spot" && routes.is_empty() {
        routes.push(CliMarketRoute {
            provider: Provider::new("binance")?,
            connection: CliMarketOnceProvider::BinanceSpotRest,
            observation_kinds: standard_capabilities(),
        });
    }
    if market_type == "option" && routes.is_empty() {
        routes.push(CliMarketRoute {
            provider: Provider::new("binance")?,
            connection: CliMarketOnceProvider::BinanceOptionsRest,
            observation_kinds: option_capabilities(),
        });
    }
    Ok(routes)
}

pub fn compose_standalone_market(
    workspace_root: Option<&Path>,
    request: &CliMarketOnceRequest,
) -> Result<CliMarketApplication, Box<dyn std::error::Error>> {
    let key = ConnectionKey::new("market-cli-once")?;
    let connection = match request.connection {
        CliMarketOnceProvider::BinanceSpotRest => {
            let configured = configured_binding(workspace_root, request.connection)?;
            let configured_endpoint = match configured {
                Some(MarketProviderBinding::BinanceSpot { endpoint, .. }) => endpoint,
                _ => None,
            };
            DirectMarketConnection::BinanceSpot(BinanceSpotRestConnection::new(
                key,
                BinanceRestConfig {
                    environment: "public".into(),
                    endpoint: request
                        .endpoint
                        .clone()
                        .or(configured_endpoint)
                        .unwrap_or_else(|| "https://api.binance.com".into()),
                    credential: None,
                },
            )?)
        },
        CliMarketOnceProvider::BinanceEquityRest => {
            let workspace = Workspace::open(
                workspace_root.ok_or("Binance equity snapshots require --workspace")?,
            )?;
            let configured = configured_binding(Some(workspace.root()), request.connection)?
                .or_else(|| {
                    first_binding(&workspace, |binding| {
                        matches!(binding, MarketProviderBinding::BinanceEquity { .. })
                    })
                })
                .ok_or("Binance equity Market source does not exist")?;
            let MarketProviderBinding::BinanceEquity {
                credential_id,
                endpoint,
                ..
            } = configured
            else {
                return Err("selected Market source is not Binance equity".into());
            };
            let credentials_root =
                workspace.existing_path(&["config", "credentials"], &["credentials"])?;
            let requested_credential = request.credential_id.as_deref().unwrap_or(&credential_id);
            let credential = load_workspace_credential(
                &credentials_root,
                "binance",
                Some(requested_credential),
            )?
            .ok_or("Binance equity workspace credential does not exist")?;
            if credential.api_key.is_empty() {
                return Err("Binance equity workspace credential has no API key".into());
            }
            DirectMarketConnection::BinanceEquity(BinanceStocksRestConnection::new(
                key,
                BinanceRestConfig {
                    environment: "public".into(),
                    endpoint: request
                        .endpoint
                        .clone()
                        .or(endpoint)
                        .unwrap_or_else(|| "https://api.binance.com".into()),
                    credential: Some(BinanceCredential {
                        principal_id: requested_credential.into(),
                        api_key: secrecy::SecretString::from(credential.api_key),
                        secret: credential.secret,
                    }),
                },
            )?)
        },
        CliMarketOnceProvider::BinanceOptionsRest => {
            DirectMarketConnection::BinanceOptions(BinanceOptionsRestConnection::new(
                key,
                BinanceRestConfig {
                    environment: "public".into(),
                    endpoint: request
                        .endpoint
                        .clone()
                        .unwrap_or_else(|| "https://eapi.binance.com".into()),
                    credential: None,
                },
            )?)
        },
        CliMarketOnceProvider::MassiveRest => {
            let workspace = Workspace::open(
                workspace_root.ok_or("Massive equity snapshots require --workspace")?,
            )?;
            let configured = configured_binding(Some(workspace.root()), request.connection)?
                .or_else(|| {
                    first_binding(&workspace, |binding| {
                        matches!(
                            binding,
                            MarketProviderBinding::Massive {
                                product: MassiveMarketProduct::Equity,
                                ..
                            }
                        )
                    })
                })
                .ok_or("Massive equity Market source does not exist")?;
            let MarketProviderBinding::Massive {
                product: MassiveMarketProduct::Equity,
                credential_id,
                endpoint,
                ..
            } = configured
            else {
                return Err("selected Market source is not Massive equity".into());
            };
            let credentials_root =
                workspace.existing_path(&["config", "credentials"], &["credentials"])?;
            let api_key = load_workspace_credential(
                &credentials_root,
                "massive",
                request.credential_id.as_deref().or(Some(&credential_id)),
            )?
            .ok_or("Massive workspace credential does not exist")?
            .api_key;
            DirectMarketConnection::MassiveEquity(MassiveRestConnection::new(
                key,
                MassiveRestConfig {
                    environment: "public".into(),
                    endpoint: request
                        .endpoint
                        .clone()
                        .or(endpoint)
                        .unwrap_or_else(|| "https://api.massive.com".into()),
                    api_key: secrecy::SecretString::new(api_key.into()),
                    instrument_query: MassiveInstrumentQuery::equities(),
                },
            )?)
        },
    };
    Ok(CliMarketApplication::with_direct_connection(
        workspace_root,
        connection,
    ))
}

fn configured_binding(
    workspace_root: Option<&Path>,
    connection: CliMarketOnceProvider,
) -> Result<Option<MarketProviderBinding>, Box<dyn std::error::Error>> {
    let Some(workspace_root) = workspace_root else {
        return Ok(None);
    };
    let workspace = Workspace::open(workspace_root)?;
    Ok(MarketConfig::load(&workspace)?
        .providers
        .into_values()
        .find(|binding| {
            binding.enabled() && direct_source(binding).is_some_and(|value| value.0 == connection)
        }))
}

fn provider_name(binding: &MarketProviderBinding) -> Option<&'static str> {
    direct_source(binding).map(|(connection, _, _)| match connection {
        CliMarketOnceProvider::BinanceSpotRest
        | CliMarketOnceProvider::BinanceEquityRest
        | CliMarketOnceProvider::BinanceOptionsRest => "binance",
        CliMarketOnceProvider::MassiveRest => "massive",
    })
}

fn first_binding(
    workspace: &Workspace,
    predicate: impl Fn(&MarketProviderBinding) -> bool,
) -> Option<MarketProviderBinding> {
    MarketConfig::load(workspace)
        .ok()?
        .providers
        .into_values()
        .find(|binding| binding.enabled() && predicate(binding))
}

fn direct_source(
    binding: &MarketProviderBinding,
) -> Option<(CliMarketOnceProvider, &'static str, Vec<ObservationKind>)> {
    match binding {
        MarketProviderBinding::BinanceSpot { .. } => Some((
            CliMarketOnceProvider::BinanceSpotRest,
            "spot",
            standard_capabilities(),
        )),
        MarketProviderBinding::BinanceEquity { .. } => Some((
            CliMarketOnceProvider::BinanceEquityRest,
            "equity",
            vec![ObservationKind::Quote],
        )),
        MarketProviderBinding::BinanceDerivatives {
            product: BinanceDerivativeProduct::Options,
            ..
        } => Some((
            CliMarketOnceProvider::BinanceOptionsRest,
            "option",
            option_capabilities(),
        )),
        MarketProviderBinding::Massive {
            product: MassiveMarketProduct::Equity,
            ..
        } => Some((
            CliMarketOnceProvider::MassiveRest,
            "equity",
            vec![
                ObservationKind::Quote,
                ObservationKind::Trade,
                ObservationKind::Bar,
            ],
        )),
        _ => None,
    }
}

fn standard_capabilities() -> Vec<ObservationKind> {
    vec![
        ObservationKind::Quote,
        ObservationKind::Trade,
        ObservationKind::Bar,
        ObservationKind::OrderBook,
    ]
}

fn option_capabilities() -> Vec<ObservationKind> {
    let mut values = standard_capabilities();
    values.push(ObservationKind::OptionGreeks);
    values
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn equity_route_discovery_filters_by_observation_capability() {
        let directory = tempfile::tempdir().unwrap();
        std::fs::write(
            directory.path().join("workspace.toml"),
            r#"version = 1
workspace_id = "test"

[[market.providers]]
type = "binance-equity"
credential_id = "binance"

[[market.providers]]
type = "massive"
product = "equity"
credential_id = "massive"
"#,
        )
        .unwrap();

        let quotes =
            standalone_market_routes(Some(directory.path()), "equity", ObservationKind::Quote)
                .unwrap();
        let trades =
            standalone_market_routes(Some(directory.path()), "equity", ObservationKind::Trade)
                .unwrap();

        assert_eq!(
            quotes
                .iter()
                .map(|route| route.provider.as_str())
                .collect::<Vec<_>>(),
            vec!["binance", "massive"]
        );
        assert_eq!(
            trades
                .iter()
                .map(|route| route.provider.as_str())
                .collect::<Vec<_>>(),
            vec!["massive"]
        );
    }

    #[test]
    fn legacy_named_sources_fail_with_an_actionable_configuration_error() {
        let directory = tempfile::tempdir().unwrap();
        std::fs::write(
            directory.path().join("workspace.toml"),
            r#"version = 1
workspace_id = "test"

[market.sources.massive-equity]
type = "massive"
product = "equity"
credential_id = "massive"
"#,
        )
        .unwrap();

        let error =
            standalone_market_routes(Some(directory.path()), "equity", ObservationKind::Quote)
                .unwrap_err();

        assert!(error.to_string().contains("unknown field `sources`"));
        assert!(error.to_string().contains("providers"));
    }
}
