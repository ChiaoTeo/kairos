use kairos_conflux::{
    BinanceCredential, BinanceRestConfig, ConnectionKey, IbkrOrderConfig, OkxCredential,
};
use kairos_credentials::{CredentialRecord, CredentialStore};
use kairos_workspace::Workspace;
use secrecy::SecretString;

use crate::application::{CliExecutionApplication, StandaloneExecutionBinding};
use crate::services::direct::{
    binance_connection, ibkr_connection, normalize, okx_connection, okx_rest_config,
};

pub fn compose_standalone_execution(
    workspace: &Workspace,
    binding: StandaloneExecutionBinding,
) -> Result<CliExecutionApplication, Box<dyn std::error::Error>> {
    let provider = normalize(&binding.provider);
    let execution_channel = effective_execution_channel(&provider, &binding);
    validate_direct_capability(&provider, &execution_channel)?;
    validate_environment_endpoint(&provider, &binding.environment, &binding.base_url)?;
    if provider == "ibkr" {
        validate_ibkr_environment_port(&binding.environment, binding.port)?;
    }
    let credential = load_credential(workspace, &binding, &provider)?;
    let key = ConnectionKey::new(format!(
        "execution.direct.{}.{}",
        binding.account_id, binding.segment_key
    ))?;
    let connection = match provider.as_str() {
        "binance" => binance_connection(
            &execution_channel,
            key,
            BinanceRestConfig {
                environment: binding.environment.clone(),
                endpoint: binding.base_url.clone(),
                credential: Some(BinanceCredential {
                    principal_id: binding.remote_account_id.clone(),
                    api_key: credential.0.clone(),
                    secret: credential.1.clone(),
                }),
            },
        )?,
        "okx" | "okex" => okx_connection(
            key,
            okx_rest_config(
                binding.environment.clone(),
                binding.base_url.clone(),
                OkxCredential {
                    principal_id: binding.remote_account_id.clone(),
                    api_key: credential.0.clone(),
                    secret: credential.1.clone(),
                    passphrase: credential.2.clone(),
                },
            ),
        )?,
        "ibkr" => ibkr_connection(
            key,
            IbkrOrderConfig {
                environment: binding.environment.clone(),
                host: binding.host.clone(),
                port: binding.port,
                client_id: binding.client_id,
                account_id: binding.remote_account_id.clone(),
            },
        )?,
        value => {
            return Err(format!(
                "standalone direct order capability is unavailable for provider {value}"
            )
            .into());
        },
    };
    CliExecutionApplication::new(binding, connection).map_err(Into::into)
}

fn validate_ibkr_environment_port(
    environment: &str,
    port: u16,
) -> Result<(), Box<dyn std::error::Error>> {
    let environment = normalize(environment);
    let live = matches!(environment.as_str(), "live" | "production" | "prod");
    let non_live = matches!(
        environment.as_str(),
        "test" | "testnet" | "demo" | "sandbox" | "paper"
    );
    if live && matches!(port, 4002 | 7497) {
        return Err(
            format!("live IBKR account cannot use the standard paper-trading port {port}").into(),
        );
    }
    if non_live && matches!(port, 4001 | 7496) {
        return Err(format!(
            "non-live IBKR account cannot use the standard live-trading port {port}"
        )
        .into());
    }
    Ok(())
}

fn effective_execution_channel(provider: &str, binding: &StandaloneExecutionBinding) -> String {
    let execution_channel = normalize(&binding.execution_channel);
    if provider != "binance" || execution_channel != "margin" {
        return execution_channel;
    }
    match binding.trading_mode.as_deref().map(normalize).as_deref() {
        Some("isolated") | Some("isolated-margin") => "isolated-margin".into(),
        Some("cross") | Some("cross-margin") => "cross-margin".into(),
        _ => execution_channel,
    }
}

fn validate_direct_capability(
    provider: &str,
    execution_channel: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    let execution_channel = normalize(execution_channel);
    let supported = match provider {
        "binance" => matches!(
            execution_channel.as_str(),
            "spot"
                | "margin"
                | "cross-margin"
                | "usd-m-futures"
                | "coin-m-futures"
                | "option"
                | "options"
                | "equity"
                | "stocks"
        ),
        "okx" | "okex" => matches!(
            execution_channel.as_str(),
            "spot" | "margin" | "swap" | "futures" | "option" | "options"
        ),
        "ibkr" => matches!(execution_channel.as_str(), "equity" | "stocks" | "spot"),
        _ => false,
    };
    if supported {
        return Ok(());
    }
    Err(format!(
        "standalone direct order capability is unavailable for {provider}/{execution_channel}"
    )
    .into())
}

fn validate_environment_endpoint(
    provider: &str,
    environment: &str,
    endpoint: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    let environment = normalize(environment);
    let endpoint = endpoint.trim().to_ascii_lowercase();
    let is_live = matches!(environment.as_str(), "live" | "production" | "prod");
    let is_test = matches!(
        environment.as_str(),
        "test" | "testnet" | "demo" | "sandbox" | "paper"
    );
    match provider {
        "binance" => {
            if !is_live && !is_test {
                return Err(format!(
                    "standalone Binance orders do not recognize environment {environment}"
                )
                .into());
            }
            let endpoint_is_test = endpoint.contains("test") || endpoint.contains("demo");
            if is_live && endpoint_is_test {
                return Err("live Binance account cannot use a test/demo endpoint".into());
            }
            if is_test && !endpoint_is_test {
                return Err(
                    "non-live Binance direct orders require an explicit test/demo base_url".into(),
                );
            }
        },
        "okx" | "okex" => {
            if !is_live {
                return Err(
                    "standalone OKX demo orders are unavailable until simulated-trading authentication is implemented"
                        .into(),
                );
            }
        },
        "ibkr" => {
            if !is_live && !is_test {
                return Err(format!(
                    "standalone IBKR orders do not recognize environment {environment}"
                )
                .into());
            }
        },
        _ => {},
    }
    Ok(())
}

fn load_credential(
    workspace: &Workspace,
    binding: &StandaloneExecutionBinding,
    provider: &str,
) -> Result<(SecretString, SecretString, SecretString), Box<dyn std::error::Error>> {
    if provider == "ibkr" {
        return Ok(("".into(), "".into(), "".into()));
    }
    let credential_id = binding
        .credential_id
        .as_deref()
        .ok_or_else(|| format!("account {} has no credential binding", binding.account_id))?;
    let path = workspace.existing_credentials_root()?;
    let store = CredentialStore::load(path)?;
    let credential = store
        .credentials
        .iter()
        .find(|value| value.credential_id == credential_id)
        .ok_or_else(|| format!("credential not found: {credential_id}"))?;
    if !credential.provider.eq_ignore_ascii_case(provider) {
        return Err(format!(
            "credential {credential_id} belongs to {}, not {provider}",
            credential.provider
        )
        .into());
    }
    validate_credential(credential, binding, provider)?;
    Ok((
        credential.api_key_value().unwrap_or_default().into(),
        credential.secret_value().unwrap_or_default().into(),
        credential.passphrase_value().unwrap_or_default().into(),
    ))
}

fn validate_credential(
    credential: &CredentialRecord,
    binding: &StandaloneExecutionBinding,
    provider: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    let role = normalize(&credential.role);
    let binding_role = normalize(&binding.credential_role);
    let trade_capable = |value: &str| matches!(value, "trade" | "trading" | "transfer" | "admin");
    if trade_capable(&binding_role) && !trade_capable(&role) {
        return Err(format!(
            "credential {} does not provide trade permission",
            credential.credential_id
        )
        .into());
    }
    let api_key = credential.api_key_value().unwrap_or_default();
    let secret = credential.secret_value().unwrap_or_default();
    if api_key.trim().is_empty() || secret.trim().is_empty() {
        return Err(format!(
            "credential {} is missing provider authentication fields",
            credential.credential_id
        )
        .into());
    }
    if matches!(provider, "okx" | "okex")
        && credential
            .passphrase_value()
            .unwrap_or_default()
            .trim()
            .is_empty()
    {
        return Err(format!(
            "credential {} is missing the OKX passphrase",
            credential.credential_id
        )
        .into());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use kairos_credentials::CredentialRecord;

    use super::{
        effective_execution_channel, validate_credential, validate_direct_capability,
        validate_environment_endpoint, validate_ibkr_environment_port,
    };
    use crate::application::StandaloneExecutionBinding;

    fn binding(role: &str) -> StandaloneExecutionBinding {
        StandaloneExecutionBinding {
            account_id: "main".into(),
            remote_account_id: "remote-main".into(),
            provider: "binance".into(),
            environment: "live".into(),
            segment_key: "spot".into(),
            execution_channel: "spot".into(),
            trading_mode: None,
            credential_id: Some("credential-main".into()),
            credential_role: role.into(),
            base_url: "https://api.binance.com".into(),
            host: String::new(),
            port: 0,
            client_id: 0,
            isolated_symbol: None,
        }
    }

    #[test]
    fn direct_environment_never_silently_crosses_live_and_test_endpoints() {
        assert!(
            validate_environment_endpoint("binance", "live", "https://api.binance.com").is_ok()
        );
        assert!(
            validate_environment_endpoint("binance", "testnet", "https://testnet.binance.example",)
                .is_ok()
        );
        assert!(
            validate_environment_endpoint("binance", "testnet", "https://api.binance.com")
                .unwrap_err()
                .to_string()
                .contains("explicit test/demo base_url")
        );
        assert!(
            validate_environment_endpoint("binance", "live", "https://testnet.binance.example")
                .unwrap_err()
                .to_string()
                .contains("live Binance")
        );
        assert!(
            validate_environment_endpoint("okx", "demo", "https://www.okx.com")
                .unwrap_err()
                .to_string()
                .contains("simulated-trading")
        );
        assert!(validate_ibkr_environment_port("live", 4001).is_ok());
        assert!(validate_ibkr_environment_port("paper", 4002).is_ok());
        assert!(validate_ibkr_environment_port("live", 4002).is_err());
        assert!(validate_ibkr_environment_port("paper", 4001).is_err());
    }

    #[test]
    fn unsupported_execution_channels_fail_before_connection_or_credential_fallback() {
        assert!(validate_direct_capability("binance", "spot").is_ok());
        assert!(validate_direct_capability("okx", "swap").is_ok());
        assert!(validate_direct_capability("ibkr", "equity").is_ok());
        assert!(
            validate_direct_capability("binance", "isolated_margin")
                .unwrap_err()
                .to_string()
                .contains("capability is unavailable")
        );
        assert!(
            validate_direct_capability("paper", "spot")
                .unwrap_err()
                .to_string()
                .contains("paper/spot")
        );
    }

    #[test]
    fn account_margin_mode_is_part_of_binance_direct_routing() {
        let mut value = binding("trade");
        value.execution_channel = "margin".into();
        value.trading_mode = Some("isolated".into());
        let execution_channel = effective_execution_channel("binance", &value);
        assert_eq!(execution_channel, "isolated-margin");
        assert!(validate_direct_capability("binance", &execution_channel).is_err());

        value.trading_mode = Some("cross".into());
        let execution_channel = effective_execution_channel("binance", &value);
        assert_eq!(execution_channel, "cross-margin");
        assert!(validate_direct_capability("binance", &execution_channel).is_ok());
    }

    #[test]
    fn direct_write_revalidates_credential_permission_and_required_secrets() {
        let credential = CredentialRecord::new(
            "credential-main",
            "binance",
            "readonly",
            [
                ("api_key".to_owned(), "key".to_owned()),
                ("api_secret".to_owned(), "secret".to_owned()),
            ],
        )
        .unwrap();
        assert!(
            validate_credential(&credential, &binding("trade"), "binance")
                .unwrap_err()
                .to_string()
                .contains("trade permission")
        );

        let missing_secret = CredentialRecord::new(
            "credential-main",
            "binance",
            "trade",
            [("api_key".to_owned(), "key".to_owned())],
        )
        .unwrap();
        assert!(
            validate_credential(&missing_secret, &binding("trade"), "binance")
                .unwrap_err()
                .to_string()
                .contains("authentication fields")
        );
    }
}
