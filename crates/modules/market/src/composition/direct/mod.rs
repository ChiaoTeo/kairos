//! Composition for one short-lived standalone Market provider query.

use std::path::Path;

use kairos_conflux::{
    BinanceCredential, BinanceOptionsRestConnection, BinanceRestConfig, BinanceSpotRestConnection,
    BinanceStocksRestConnection, BinanceUsdMRestConnection, ConnectionKey, MassiveInstrumentQuery,
    MassiveRestConfig, MassiveRestConnection,
};
use kairos_credentials::CredentialStore;
use kairos_primitives::market::{ObservationKind, Provider};
use kairos_workspace::Workspace;

use super::config::{
    BinanceDerivativeProduct, MarketConfig, MarketProviderBinding, MassiveMarketProduct,
};
use crate::application::{
    CliMarketApplication, CliMarketHistoricalDownloadRequest, CliMarketHistoricalMarketType,
    CliMarketHistoricalProvider, CliMarketOnceProvider, CliMarketOnceRequest, CliMarketRoute,
};
use crate::services::direct::{DirectHistoricalConnection, DirectMarketConnection};

/// Select the concrete short-lived provider used by one historical download.
pub fn compose_historical_market(
    workspace_root: Option<&Path>,
    request: &CliMarketHistoricalDownloadRequest,
) -> Result<CliMarketApplication, Box<dyn std::error::Error>> {
    let endpoint = request
        .endpoint
        .clone()
        .unwrap_or_else(|| match request.provider {
            CliMarketHistoricalProvider::Massive => "https://api.massive.com".into(),
            CliMarketHistoricalProvider::Binance => "https://data-api.binance.vision".into(),
        });
    let key = ConnectionKey::new("market-history")?;
    let connection = match request.provider {
        CliMarketHistoricalProvider::Massive => {
            let workspace = workspace_root.map(Workspace::open).transpose()?;
            let product = match request.market_type {
                CliMarketHistoricalMarketType::Equity => "equity",
                CliMarketHistoricalMarketType::Option => "options",
                CliMarketHistoricalMarketType::Spot => {
                    return Err("Massive historical Spot is unsupported".into());
                },
            };
            let profile = workspace
                .as_ref()
                .map(|workspace| {
                    query_profile(
                        workspace,
                        "massive",
                        product,
                        request.credential_id.as_deref(),
                    )
                })
                .transpose()?
                .flatten();
            let endpoint = request
                .endpoint
                .clone()
                .or_else(|| {
                    profile.as_ref().and_then(|profile| {
                        profile
                            .endpoint_for("market-query", Some(product))
                            .map(str::to_owned)
                    })
                })
                .unwrap_or(endpoint);
            let api_key = if let Some(value) = request.api_key.clone() {
                value
            } else {
                let workspace = workspace
                    .as_ref()
                    .ok_or("Massive download requires --workspace or the deprecated --api-key")?;
                let profile = profile.as_ref().ok_or(
                    "Massive download requires an enabled Integration market-query connection",
                )?;
                CredentialStore::for_workspace(workspace)?
                    .find_provider("massive", Some(&profile.credential_id))
                    .and_then(|credential| credential.api_key_value())
                    .ok_or("Massive workspace credential does not exist")?
            };
            DirectHistoricalConnection::Massive(MassiveRestConnection::new(
                key,
                MassiveRestConfig {
                    environment: profile
                        .as_ref()
                        .map(|profile| profile.environment.clone())
                        .unwrap_or_else(|| "public".into()),
                    endpoint,
                    api_key: secrecy::SecretString::new(api_key.into()),
                    instrument_query: match request.market_type {
                        CliMarketHistoricalMarketType::Equity => MassiveInstrumentQuery::equities(),
                        CliMarketHistoricalMarketType::Option => {
                            MassiveInstrumentQuery::options(None)
                        },
                        CliMarketHistoricalMarketType::Spot => {
                            return Err("Massive historical Spot is unsupported".into());
                        },
                    },
                },
            )?)
        },
        CliMarketHistoricalProvider::Binance => {
            DirectHistoricalConnection::Binance(BinanceSpotRestConnection::new(
                key,
                BinanceRestConfig {
                    environment: "public".into(),
                    endpoint,
                    credential: None,
                },
            )?)
        },
    };
    let reference = if let (
        CliMarketHistoricalProvider::Massive,
        crate::application::CliMarketHistoricalDataKind::Quote,
        Some(workspace_root),
    ) = (request.provider, request.data_kind, workspace_root)
    {
        let workspace = Workspace::open(workspace_root)?;
        let database = workspace.child(&["state", "reference", "reference.sqlite"])?;
        if database.try_exists()? {
            Some(kairos_reference_contract::ReferenceCatalog::open(
                &database,
            )?)
        } else {
            None
        }
    } else {
        None
    };
    Ok(CliMarketApplication::with_historical_connection(
        workspace_root,
        connection,
        reference,
    ))
}

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
            if matches!(binding, MarketProviderBinding::Massive { .. }) {
                continue;
            }
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
    if market_type == "equity" {
        if let Some(workspace_root) = workspace_root {
            let workspace = Workspace::open(workspace_root)?;
            let capabilities = vec![
                ObservationKind::Quote,
                ObservationKind::Trade,
                ObservationKind::Bar,
            ];
            if capabilities.contains(&observation_kind)
                && query_profile(&workspace, "massive", "equity", None)?.is_some()
            {
                routes.push(CliMarketRoute {
                    provider: Provider::new("massive")?,
                    connection: CliMarketOnceProvider::MassiveRest,
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
                Some(MarketProviderBinding::BinanceSpot {
                    connection_id,
                    endpoint,
                    ..
                }) => provider_profile(workspace_root, connection_id.as_deref())?
                    .and_then(|value| {
                        value
                            .endpoint_for("market-query", Some("spot"))
                            .map(str::to_owned)
                    })
                    .or(endpoint),
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
        CliMarketOnceProvider::BinanceUsdMRest => {
            let configured = configured_binding(workspace_root, request.connection)?;
            let configured_endpoint = match configured {
                Some(MarketProviderBinding::BinanceDerivatives {
                    product: BinanceDerivativeProduct::UsdMFutures,
                    connection_id,
                    endpoint,
                    ..
                }) => provider_profile(workspace_root, connection_id.as_deref())?
                    .and_then(|value| {
                        value
                            .endpoint_for("market-query", Some("usd-m-futures"))
                            .map(str::to_owned)
                    })
                    .or(endpoint),
                _ => None,
            };
            DirectMarketConnection::BinanceUsdM(BinanceUsdMRestConnection::new(
                key,
                BinanceRestConfig {
                    environment: "public".into(),
                    endpoint: request
                        .endpoint
                        .clone()
                        .or(configured_endpoint)
                        .unwrap_or_else(|| "https://fapi.binance.com".into()),
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
                connection_id,
                credential_id,
                endpoint,
                ..
            } = configured
            else {
                return Err("selected Market source is not Binance equity".into());
            };
            let profile = provider_profile(Some(workspace.root()), connection_id.as_deref())?;
            let requested_credential = request
                .credential_id
                .as_deref()
                .or_else(|| profile.as_ref().map(|value| value.credential_id.as_str()))
                .or(credential_id.as_deref())
                .ok_or("Binance equity source requires a connection or credential")?;
            let credentials = CredentialStore::for_workspace(&workspace)?;
            let credential = credentials
                .find_provider("binance", Some(requested_credential))
                .ok_or("Binance equity workspace credential does not exist")?;
            let api_key = credential
                .value("api_key")
                .cloned()
                .ok_or("Binance equity workspace credential has no API key")?;
            let secret = credential.value("api_secret").cloned().unwrap_or_default();
            DirectMarketConnection::BinanceEquity(BinanceStocksRestConnection::new(
                key,
                BinanceRestConfig {
                    environment: "public".into(),
                    endpoint: request
                        .endpoint
                        .clone()
                        .or_else(|| {
                            profile.as_ref().and_then(|value| {
                                value
                                    .endpoint_for("market-query", Some("equity"))
                                    .map(str::to_owned)
                            })
                        })
                        .or(endpoint)
                        .unwrap_or_else(|| "https://api.binance.com".into()),
                    credential: Some(BinanceCredential {
                        principal_id: requested_credential.into(),
                        api_key,
                        secret,
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
            let profile = query_profile(
                &workspace,
                "massive",
                "equity",
                request.credential_id.as_deref(),
            )?
            .ok_or(
                "Massive equity query requires an enabled Integration market-query connection",
            )?;
            let credentials = CredentialStore::for_workspace(&workspace)?;
            let api_key = credentials
                .find_provider("massive", Some(&profile.credential_id))
                .and_then(|credential| credential.value("api_key").cloned())
                .ok_or("Massive workspace credential does not exist")?;
            DirectMarketConnection::MassiveEquity(MassiveRestConnection::new(
                key,
                MassiveRestConfig {
                    environment: profile.environment.clone(),
                    endpoint: request
                        .endpoint
                        .clone()
                        .or_else(|| {
                            profile
                                .endpoint_for("market-query", Some("equity"))
                                .map(str::to_owned)
                        })
                        .ok_or("Massive query endpoint is missing")?,
                    api_key,
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

fn query_profile(
    workspace: &Workspace,
    provider: &str,
    product: &str,
    credential_id: Option<&str>,
) -> Result<
    Option<kairos_integration::composition::ProviderConnectionProfile>,
    Box<dyn std::error::Error>,
> {
    use kairos_integration::composition::ProviderConnectionProfile;
    let root = ProviderConnectionProfile::canonical_root(workspace.root());
    if !root.try_exists()? {
        return Ok(None);
    }
    let mut selected = None;
    for entry in std::fs::read_dir(&root)? {
        let path = entry?.path();
        if path.extension().and_then(|value| value.to_str()) != Some("toml") {
            continue;
        }
        let id = path
            .file_stem()
            .and_then(|value| value.to_str())
            .ok_or("invalid connection filename")?;
        let profile = ProviderConnectionProfile::load(&root, id)?;
        if profile.provider != provider
            || !profile.products.iter().any(|value| value == product)
            || credential_id.is_some_and(|value| value != profile.credential_id)
        {
            continue;
        }
        if profile
            .require(provider, Some(product), "market-query")
            .is_err()
        {
            continue;
        }
        if selected.is_some() {
            return Err(
                "multiple Integration market-query connections match; select a unique credential"
                    .into(),
            );
        }
        selected = Some(profile);
    }
    Ok(selected)
}

fn provider_profile(
    workspace_root: Option<&Path>,
    connection_id: Option<&str>,
) -> Result<
    Option<kairos_integration::composition::ProviderConnectionProfile>,
    Box<dyn std::error::Error>,
> {
    let Some(connection_id) = connection_id else {
        return Ok(None);
    };
    let workspace_root = workspace_root.ok_or("provider connection requires a Workspace")?;
    let root =
        kairos_integration::composition::ProviderConnectionProfile::canonical_root(workspace_root);
    Ok(Some(
        kairos_integration::composition::ProviderConnectionProfile::load(&root, connection_id)?,
    ))
}

fn provider_name(binding: &MarketProviderBinding) -> Option<&'static str> {
    direct_source(binding).map(|(connection, _, _)| match connection {
        CliMarketOnceProvider::BinanceSpotRest
        | CliMarketOnceProvider::BinanceUsdMRest
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
            product: BinanceDerivativeProduct::UsdMFutures,
            ..
        } => Some((
            CliMarketOnceProvider::BinanceUsdMRest,
            "perpetual",
            vec![ObservationKind::Quote, ObservationKind::OrderBook],
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
    fn query_profile_uses_private_connection_without_a_market_source_and_rejects_ambiguity() {
        let directory = tempfile::tempdir().unwrap();
        std::fs::write(
            directory.path().join("workspace.toml"),
            "version = 1\nworkspace_id = \"test\"\n",
        )
        .unwrap();
        let workspace = Workspace::open(directory.path()).unwrap();
        let root = kairos_integration::composition::ProviderConnectionProfile::canonical_root(
            workspace.root(),
        );
        std::fs::create_dir_all(&root).unwrap();
        let profile = |id: &str, enabled: bool| {
            format!(
                "version = 2\n[connection]\nconnection_id = \"{id}\"\nprovider = \"massive\"\nenvironment = \"private\"\nendpoint = \"https://private.example.test\"\ncredential_id = \"{id}\"\nenabled = {enabled}\nproducts = [\"equity\"]\npurposes = [\"market-query\"]\n"
            )
        };
        std::fs::write(root.join("one.toml"), profile("one", true)).unwrap();
        let selected = query_profile(&workspace, "massive", "equity", None)
            .unwrap()
            .unwrap();
        assert_eq!(
            selected.endpoint_for("market-query", Some("equity")),
            Some("https://private.example.test")
        );
        assert_eq!(selected.environment, "private");
        let routes =
            standalone_market_routes(Some(directory.path()), "equity", ObservationKind::Quote)
                .unwrap();
        assert_eq!(routes.len(), 1);
        assert_eq!(routes[0].provider.as_str(), "massive");
        assert!(
            query_profile(&workspace, "massive", "options", None)
                .unwrap()
                .is_none()
        );
        std::fs::write(root.join("two.toml"), profile("two", true)).unwrap();
        assert!(query_profile(&workspace, "massive", "equity", None).is_err());
        assert_eq!(
            query_profile(&workspace, "massive", "equity", Some("two"))
                .unwrap()
                .unwrap()
                .connection_id,
            "two"
        );
        std::fs::write(root.join("two.toml"), profile("two", false)).unwrap();
        assert_eq!(
            query_profile(&workspace, "massive", "equity", None)
                .unwrap()
                .unwrap()
                .connection_id,
            "one"
        );
    }

    #[test]
    fn historical_quote_reference_is_optional_read_only_and_not_used_for_bars() {
        let directory = tempfile::tempdir().unwrap();
        std::fs::write(
            directory.path().join("workspace.toml"),
            "version = 1\nworkspace_id = \"test\"\n",
        )
        .unwrap();
        let mut request = CliMarketHistoricalDownloadRequest {
            provider: CliMarketHistoricalProvider::Massive,
            api_key: Some("test-only-not-a-credential".into()),
            credential_id: None,
            endpoint: Some("http://127.0.0.1:1".into()),
            symbol: "AAPL".into(),
            market_type: CliMarketHistoricalMarketType::Equity,
            data_kind: crate::application::CliMarketHistoricalDataKind::Quote,
            market_id: None,
            instrument_id: Some("instrument:fixture".into()),
            network_id: None,
            start_unix_millis: 1,
            end_unix_millis: 2,
            interval: "1m".into(),
            adjusted: false,
            dataset_id: "fixture".into(),
            file: directory.path().join("quotes.jsonl"),
        };
        assert!(compose_historical_market(Some(directory.path()), &request).is_ok());
        let database = directory.path().join("state/reference/reference.sqlite");
        assert!(
            !database.exists(),
            "read composition must not create a catalog"
        );
        std::fs::create_dir_all(database.parent().unwrap()).unwrap();
        std::fs::write(&database, b"invalid database fixture").unwrap();
        assert!(compose_historical_market(Some(directory.path()), &request).is_err());
        assert_eq!(
            std::fs::read(&database).unwrap(),
            b"invalid database fixture"
        );
        request.data_kind = crate::application::CliMarketHistoricalDataKind::Bar;
        assert!(compose_historical_market(Some(directory.path()), &request).is_ok());
    }

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

"#,
        )
        .unwrap();

        let profile_root = directory
            .path()
            .join("config/integration/provider-connections");
        std::fs::create_dir_all(&profile_root).unwrap();
        std::fs::write(
            profile_root.join("massive.toml"),
            r#"version = 2
[connection]
connection_id = "massive"
provider = "massive"
environment = "private"
endpoint = "https://private.example.test"
credential_id = "massive"
products = ["equity"]
purposes = ["market-query"]
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
    fn perpetual_direct_routes_discover_enabled_binance_usdm_capabilities() {
        let directory = tempfile::tempdir().unwrap();
        std::fs::write(
            directory.path().join("workspace.toml"),
            r#"version = 1
workspace_id = "test"

[[market.providers]]
type = "binance-derivatives"
product = "usd-m-futures"
transport = "rest"
"#,
        )
        .unwrap();

        let quotes =
            standalone_market_routes(Some(directory.path()), "perpetual", ObservationKind::Quote)
                .unwrap();
        let order_books = standalone_market_routes(
            Some(directory.path()),
            "perpetual",
            ObservationKind::OrderBook,
        )
        .unwrap();
        let trades =
            standalone_market_routes(Some(directory.path()), "perpetual", ObservationKind::Trade)
                .unwrap();

        assert_eq!(quotes.len(), 1);
        assert_eq!(quotes[0].provider.as_str(), "binance");
        assert_eq!(quotes[0].connection, CliMarketOnceProvider::BinanceUsdMRest);
        assert_eq!(order_books.len(), 1);
        assert_eq!(order_books[0].provider.as_str(), "binance");
        assert!(trades.is_empty());
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
