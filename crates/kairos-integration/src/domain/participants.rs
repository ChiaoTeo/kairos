//! Identities of the external parties involved in an integration route.

#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub enum ParticipantKind {
    Exchange,
    Broker,
    DataProvider,
}

#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
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

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
pub struct IntegrationRoute {
    pub exchange: Option<ParticipantRef>,
    pub broker: Option<ParticipantRef>,
    pub data_provider: Option<ParticipantRef>,
}

impl IntegrationRoute {
    pub fn exchange(id: impl Into<String>) -> Self {
        Self::single(ParticipantKind::Exchange, id)
    }

    pub fn broker(id: impl Into<String>) -> Self {
        Self::single(ParticipantKind::Broker, id)
    }

    pub fn broker_at(broker: impl Into<String>, exchange: impl Into<String>) -> Self {
        Self {
            exchange: Some(ParticipantRef::new(ParticipantKind::Exchange, exchange).unwrap()),
            broker: Some(ParticipantRef::new(ParticipantKind::Broker, broker).unwrap()),
            data_provider: None,
        }
    }

    pub fn data_provider(id: impl Into<String>) -> Self {
        Self::single(ParticipantKind::DataProvider, id)
    }

    pub fn data_provider_for(provider: impl Into<String>, exchange: impl Into<String>) -> Self {
        Self {
            exchange: Some(ParticipantRef::new(ParticipantKind::Exchange, exchange).unwrap()),
            broker: None,
            data_provider: Some(
                ParticipantRef::new(ParticipantKind::DataProvider, provider).unwrap(),
            ),
        }
    }

    fn single(kind: ParticipantKind, id: impl Into<String>) -> Self {
        let participant = ParticipantRef::new(kind, id).unwrap();
        let mut route = Self {
            exchange: None,
            broker: None,
            data_provider: None,
        };
        match kind {
            ParticipantKind::Exchange => route.exchange = Some(participant),
            ParticipantKind::Broker => route.broker = Some(participant),
            ParticipantKind::DataProvider => route.data_provider = Some(participant),
        }
        route
    }

    pub fn validate(&self) -> Result<(), String> {
        if self.exchange.is_none() && self.broker.is_none() && self.data_provider.is_none() {
            return Err("integration route requires an exchange, broker, or data provider".into());
        }
        if self
            .exchange
            .as_ref()
            .is_some_and(|value| value.kind != ParticipantKind::Exchange)
            || self
                .broker
                .as_ref()
                .is_some_and(|value| value.kind != ParticipantKind::Broker)
            || self
                .data_provider
                .as_ref()
                .is_some_and(|value| value.kind != ParticipantKind::DataProvider)
        {
            return Err("integration route participant kind does not match its slot".into());
        }
        if self.broker.is_some() && self.data_provider.is_some() {
            return Err("integration route cannot combine a broker and data provider".into());
        }
        for participant in [
            self.exchange.as_ref(),
            self.broker.as_ref(),
            self.data_provider.as_ref(),
        ]
        .into_iter()
        .flatten()
        {
            if participant.id.trim().is_empty() {
                return Err("participant id is required".into());
            }
        }
        Ok(())
    }

    /// A gateway is selected by its primary participant. The route may also
    /// carry a venue that the broker or data provider serves.
    pub fn matches_primary(&self, requested: &Self) -> bool {
        self.primary() == requested.primary()
    }

    pub fn primary(&self) -> &ParticipantRef {
        self.broker
            .as_ref()
            .or(self.data_provider.as_ref())
            .or(self.exchange.as_ref())
            .expect("validated integration route has a participant")
    }
}

#[cfg(test)]
mod tests {
    use super::{IntegrationRoute, ParticipantKind};
    use crate::domain::{AccessScope, ConnectionSpec, IntegrationCapability, TransportKind};

    #[test]
    fn broker_route_can_identify_the_exchange_it_reaches() {
        let route = IntegrationRoute::broker_at("ibkr", "nasdaq");
        assert_eq!(route.primary().kind, ParticipantKind::Broker);
        assert_eq!(route.primary().id, "ibkr");
        assert!(route.validate().is_ok());
    }

    #[test]
    fn data_provider_is_restricted_to_public_market_capabilities() {
        let spec = ConnectionSpec {
            connection_id: "account.massive".into(),
            route: IntegrationRoute::data_provider("massive"),
            product: None,
            access: AccessScope::Private,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::AccountRead,
            credential_id: Some("massive".into()),
            asset_type: None,
        };
        assert!(spec.validate().is_err());
    }

    #[test]
    fn routes_match_by_primary_participant_when_a_venue_is_attached() {
        assert!(IntegrationRoute::broker("ibkr")
            .matches_primary(&IntegrationRoute::broker_at("ibkr", "nyse")));
        assert!(IntegrationRoute::data_provider("massive")
            .matches_primary(&IntegrationRoute::data_provider_for("massive", "nasdaq")));
    }
}
