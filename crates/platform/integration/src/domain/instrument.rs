//! Provider-owned instrument identity at the Integration boundary.

use kairos_domain_types::ProviderSymbol;

use super::ParticipantRef;

#[derive(
    Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, serde::Deserialize, serde::Serialize,
)]
pub struct ParticipantInstrumentTypeRef {
    code: String,
}

impl ParticipantInstrumentTypeRef {
    pub fn new(code: impl Into<String>) -> Result<Self, String> {
        let code = code.into();
        if code.trim().is_empty() {
            return Err("participant instrument type is required".into());
        }
        Ok(Self { code })
    }

    pub fn as_str(&self) -> &str {
        &self.code
    }
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Deserialize, serde::Serialize)]
pub struct ProviderInstrumentRef {
    pub participant: ParticipantRef,
    pub instrument_type: Option<ParticipantInstrumentTypeRef>,
    pub source_symbol: ProviderSymbol,
    /// Reference-owned access identity when the caller already resolved the
    /// provider route. Consumers must not infer it from `source_symbol`.
    #[serde(default)]
    pub market_data_access_id: Option<String>,
}

impl ProviderInstrumentRef {
    pub fn new(
        participant: ParticipantRef,
        instrument_type: Option<ParticipantInstrumentTypeRef>,
        source_symbol: impl Into<String>,
    ) -> Result<Self, String> {
        let source_symbol =
            ProviderSymbol::new(source_symbol.into()).map_err(|error| error.to_string())?;
        Ok(Self {
            participant,
            instrument_type,
            source_symbol,
            market_data_access_id: None,
        })
    }

    pub fn with_market_data_access(mut self, access_id: impl Into<String>) -> Result<Self, String> {
        let access_id = access_id.into();
        if access_id.trim().is_empty() {
            return Err("market data access id is required".into());
        }
        self.market_data_access_id = Some(access_id);
        Ok(self)
    }
}
