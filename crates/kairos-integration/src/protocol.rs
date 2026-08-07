//! Minimal contracts consumed by integration implementations.

pub trait MillisecondClock: Send + Sync {
    fn now_millis(&self) -> u64;
}

pub trait CredentialValueReader: Send + Sync {
    fn value(&self, credential_id: &str, field: &str) -> Option<String>;
}
