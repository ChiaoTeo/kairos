use secrecy::SecretString;

#[derive(Clone)]
pub struct MassiveConnectionConfig {
    pub environment: String,
    pub rest_base_url: String,
    pub api_key: SecretString,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct MassiveChannelConfig {
    pub event_queue_capacity: usize,
}
