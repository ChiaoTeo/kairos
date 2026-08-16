//! Identities of external parties that own integration capabilities.

#[derive(
    Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, serde::Deserialize, serde::Serialize,
)]
pub enum ParticipantKind {
    Exchange,
    Broker,
    DataProvider,
}

#[derive(
    Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, serde::Deserialize, serde::Serialize,
)]
pub struct ParticipantRef {
    pub kind: ParticipantKind,
    pub id: String,
}

impl ParticipantRef {
    pub fn new(kind: ParticipantKind, id: impl Into<String>) -> Result<Self, String> {
        let id = id.into();
        if id.trim().is_empty() {
            return Err("participant id is required".into());
        }
        Ok(Self { kind, id })
    }
}

#[cfg(test)]
mod tests {
    use super::{ParticipantKind, ParticipantRef};

    #[test]
    fn participant_requires_a_non_empty_native_id() {
        let participant = ParticipantRef::new(ParticipantKind::Broker, "ibkr").unwrap();
        assert_eq!(participant.kind, ParticipantKind::Broker);
        assert_eq!(participant.id, "ibkr");
        assert!(ParticipantRef::new(ParticipantKind::Exchange, " ").is_err());
    }
}
