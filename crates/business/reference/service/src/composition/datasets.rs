//! Concrete provider composition for immutable Reference datasets.

use kairos_integration::application::capabilities::reference::{
    AsyncInstrumentCatalogConnection, ExternalInstrumentKind,
};
use kairos_integration::application::credential::load_workspace_credential;
use kairos_integration::participants::massive::{
    InstrumentQuery, MassiveConnection, MassiveConnectionConfig,
};
use kairos_workspace::workspace::Workspace;
use secrecy::SecretString;

use crate::application::{
    CashDividendDatasetRequest, CashDividendDatasetResult, CashDividendInput, OptionContractInput,
    OptionContractSnapshotRequest, OptionContractSnapshotResult, ReferenceDatasetApplication,
};
use crate::domain::{ReferenceError, ReferenceResult};

#[derive(Clone, Debug)]
pub struct MassiveReferenceDatasetConfig {
    pub credential_id: String,
    pub endpoint: String,
}

pub async fn prepare_massive_option_contract_snapshot(
    workspace: &Workspace,
    config: &MassiveReferenceDatasetConfig,
    request: &OptionContractSnapshotRequest,
) -> ReferenceResult<OptionContractSnapshotResult> {
    let connection = massive_connection(workspace, config)?;
    let mut query = InstrumentQuery::options(Some(request.underlying.clone()))
        .as_of(request.as_of.clone())
        .expiration_between(
            request.expiration_start.clone(),
            request.expiration_end.clone(),
        );
    if let Some(right) = &request.option_right {
        query = query.contract_type(right.clone());
    }
    let catalog = connection
        .instrument_catalog(query)
        .fetch_instruments()
        .await
        .map_err(|error| ReferenceError::Provider(error.to_string()))?;
    let mut inputs = Vec::with_capacity(catalog.instruments.len());
    for instrument in catalog.instruments {
        if instrument.kind != ExternalInstrumentKind::Option {
            return Err(ReferenceError::Provider(
                "Massive option query returned a non-option instrument".into(),
            ));
        }
        inputs.push(OptionContractInput {
            provider_symbol: instrument.source_symbol.as_str().to_owned(),
            source_venue: instrument.source_venue,
            underlying: instrument
                .underlying
                .map(|value| value.as_str().to_owned())
                .ok_or_else(|| {
                    ReferenceError::Provider("Massive option underlying is missing".into())
                })?,
            expiry_unix_nanos: instrument
                .expiry_unix_nanos
                .map(|value| value.get())
                .ok_or_else(|| {
                    ReferenceError::Provider("Massive option expiry is missing".into())
                })?,
            strike: instrument.strike.ok_or_else(|| {
                ReferenceError::Provider("Massive option strike is missing".into())
            })?,
            option_right: instrument.option_right.ok_or_else(|| {
                ReferenceError::Provider("Massive option right is missing".into())
            })?,
            contract_multiplier: instrument.contract_value.ok_or_else(|| {
                ReferenceError::Provider("Massive option multiplier is missing".into())
            })?,
            active: instrument.active,
        });
    }
    ReferenceDatasetApplication.option_contract_snapshot(request, inputs)
}

pub async fn prepare_massive_cash_dividends(
    workspace: &Workspace,
    config: &MassiveReferenceDatasetConfig,
    request: &CashDividendDatasetRequest,
) -> ReferenceResult<CashDividendDatasetResult> {
    let connection = massive_connection(workspace, config)?;
    let dividends = connection
        .dividend_catalog()
        .fetch(&request.ticker, &request.start_date, &request.end_date)
        .await
        .map_err(|error| ReferenceError::Provider(error.to_string()))?;
    let inputs = dividends
        .into_iter()
        .map(|dividend| CashDividendInput {
            id: dividend.id,
            ticker: dividend.ticker,
            ex_dividend_date: dividend.ex_dividend_date,
            declaration_date: dividend.declaration_date,
            record_date: dividend.record_date,
            pay_date: dividend.pay_date,
            cash_amount: dividend.cash_amount,
            split_adjusted_cash_amount: dividend.split_adjusted_cash_amount,
            historical_adjustment_factor: dividend.historical_adjustment_factor,
            currency: dividend.currency,
            distribution_type: dividend.distribution_type,
            frequency: dividend.frequency,
        })
        .collect();
    ReferenceDatasetApplication.cash_dividends(request, inputs)
}

fn massive_connection(
    workspace: &Workspace,
    config: &MassiveReferenceDatasetConfig,
) -> ReferenceResult<MassiveConnection> {
    let credential = load_workspace_credential(
        &workspace.root().join("credentials"),
        "massive",
        Some(config.credential_id.as_str()),
    )
    .map_err(|error| ReferenceError::Provider(error.to_string()))?
    .ok_or_else(|| {
        ReferenceError::Provider(format!(
            "Massive credential does not exist: {}",
            config.credential_id
        ))
    })?;
    if credential.api_key.trim().is_empty() {
        return Err(ReferenceError::Provider(
            "Massive credential has no API key".into(),
        ));
    }
    MassiveConnection::connect(MassiveConnectionConfig {
        environment: "historical-reference".into(),
        rest_base_url: config.endpoint.clone(),
        api_key: SecretString::new(credential.api_key.into()),
    })
    .map_err(|error| ReferenceError::Provider(error.to_string()))
}
