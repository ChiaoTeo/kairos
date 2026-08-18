use secrecy::SecretString;

use super::InstrumentQuery;

#[derive(Clone)]
pub struct MassiveRestConfig {
    pub binding_id: String,
    pub environment: String,
    pub endpoint: String,
    pub api_key: SecretString,
    pub instrument_query: InstrumentQuery,
}

#[derive(Clone)]
pub struct MassiveWebSocketConfig {
    pub binding_id: String,
    pub environment: String,
    pub endpoint: String,
    pub api_key: SecretString,
    pub event_capacity: usize,
}
