//! Participant-owned instrument identity at the Integration boundary.

use kairos_primitives::ParticipantSymbol;

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
pub struct ParticipantInstrumentRef {
    pub participant: ParticipantRef,
    pub instrument_type: Option<ParticipantInstrumentTypeRef>,
    pub source_symbol: ParticipantSymbol,
}

impl ParticipantInstrumentRef {
    pub fn new(
        participant: ParticipantRef,
        instrument_type: Option<ParticipantInstrumentTypeRef>,
        source_symbol: impl Into<String>,
    ) -> Result<Self, String> {
        let source_symbol =
            ParticipantSymbol::new(source_symbol.into()).map_err(|error| error.to_string())?;
        Ok(Self {
            participant,
            instrument_type,
            source_symbol,
        })
    }
}
