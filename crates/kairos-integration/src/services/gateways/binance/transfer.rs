//! Binance internal-wallet transfer connection.

use std::collections::BTreeMap;

use crate::application::{Connection, ConnectionSpec, IntegrationError, TransferConnection};
use crate::domain::{AccessScope, IntegrationCapability, TransportKind};
use crate::services::connections::ManagedConnection;

use super::spot::account::BinanceSpotAccountClient;
use crate::application::{TransferRequest, TransferResult};

pub struct BinanceTransferConnection {
    connection: ManagedConnection,
    client: BinanceSpotAccountClient,
}

impl BinanceTransferConnection {
    pub fn new(
        api_key: impl Into<String>,
        secret: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Result<Self, String> {
        let client = BinanceSpotAccountClient::new(api_key, secret, base_url)
            .map_err(|error| error.to_string())?;
        let spec = ConnectionSpec {
            connection_id: "account.binance.transfer.rest".into(),
            provider: "binance".into(),
            product: None,
            access: AccessScope::Private,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::Transfer,
            credential_id: Some("binance".into()),
            asset_type: None,
        };
        Ok(Self {
            connection: ManagedConnection::new(spec, Vec::new())?,
            client,
        })
    }
}

impl Connection for BinanceTransferConnection {
    fn identity(&self) -> &crate::domain::ConnectionIdentity {
        self.connection.identity()
    }
    fn state(&self) -> &crate::domain::ConnectionState {
        self.connection.state()
    }
    fn start(&mut self) -> Result<(), String> {
        self.connection.start()
    }
    fn stop(&mut self) -> Result<(), String> {
        self.connection.stop()
    }
    fn reconnect(&mut self) -> Result<(), String> {
        self.connection.reconnect()
    }
    fn health(&self) -> crate::domain::ConnectionHealth {
        self.connection.health()
    }
}

impl TransferConnection for BinanceTransferConnection {
    fn transfer(&mut self, request: &TransferRequest) -> Result<TransferResult, String> {
        request.validate()?;
        self.start()?;
        let mut params = BTreeMap::new();
        params.insert(
            "type".into(),
            transfer_type(request).map_err(|error| error.to_string())?,
        );
        params.insert("asset".into(), request.asset.trim().to_ascii_uppercase());
        params.insert("amount".into(), format_decimal(request.amount));
        let payload = self
            .client
            .signed_post("/sapi/v1/asset/transfer", params)
            .map_err(|error| error.to_string())?;
        Ok(TransferResult {
            accepted: true,
            reference_id: payload
                .get("tranId")
                .map(|value| value.to_string().trim_matches('"').to_owned()),
            reason: String::new(),
        })
    }
}

fn transfer_type(request: &TransferRequest) -> Result<String, IntegrationError> {
    let source = wallet_name(&request.source.segment_key)?;
    let destination = wallet_name(&request.destination.segment_key)?;
    match (source, destination) {
        ("spot", "usd_m_futures") => Ok("MAIN_UMFUTURE".into()),
        ("usd_m_futures", "spot") => Ok("UMFUTURE_MAIN".into()),
        ("spot", "coin_m_futures") => Ok("MAIN_CMFUTURE".into()),
        ("coin_m_futures", "spot") => Ok("CMFUTURE_MAIN".into()),
        ("spot", "funding") => Ok("MAIN_FUNDING".into()),
        ("funding", "spot") => Ok("FUNDING_MAIN".into()),
        _ => Err(IntegrationError::UnsupportedOperation),
    }
}

fn wallet_name(segment: &str) -> Result<&str, IntegrationError> {
    let normalized = segment.trim().to_ascii_lowercase().replace('-', "_");
    match normalized.as_str() {
        "spot" | "main" => Ok("spot"),
        "usd_m_futures" | "usdm_futures" | "um_futures" => Ok("usd_m_futures"),
        "coin_m_futures" | "coinm_futures" | "cm_futures" => Ok("coin_m_futures"),
        "funding" | "funding_wallet" => Ok("funding"),
        _ => Err(IntegrationError::InvalidRequest(format!(
            "unsupported Binance wallet segment: {segment}"
        ))),
    }
}

fn format_decimal(value: crate::domain::ExternalDecimal) -> String {
    if value.scale == 0 {
        return value.mantissa.to_string();
    }
    let negative = value.mantissa < 0;
    let digits = value.mantissa.abs().to_string();
    let scale = value.scale as usize;
    let padded = format!(
        "{}{}",
        "0".repeat(scale.saturating_sub(digits.len())),
        digits
    );
    let split = padded.len() - scale;
    format!(
        "{}{}.{}",
        if negative { "-" } else { "" },
        &padded[..split],
        &padded[split..]
    )
}
