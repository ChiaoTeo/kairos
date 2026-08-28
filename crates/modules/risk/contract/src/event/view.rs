use kairos_protocol::generated::kairos::risk::v_2 as fb;
use kairos_protocol::{BorrowedEventView, BusinessEventKind};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RiskEventKind {
    DecisionMade,
    ReservationReserved,
    ReservationConsumed,
    ReservationReleased,
    ReservationExpired,
    CircuitOpened,
    CircuitClosed,
}

impl RiskEventKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::DecisionMade => "decision_made",
            Self::ReservationReserved => "reservation_reserved",
            Self::ReservationConsumed => "reservation_consumed",
            Self::ReservationReleased => "reservation_released",
            Self::ReservationExpired => "reservation_expired",
            Self::CircuitOpened => "circuit_opened",
            Self::CircuitClosed => "circuit_closed",
        }
    }
}

impl BusinessEventKind for RiskEventKind {
    fn as_str(self) -> &'static str {
        self.as_str()
    }
}

pub enum DecodedRiskEvent<'a> {
    DecisionMade(fb::RiskDecisionMade<'a>),
    ReservationReserved(fb::ReservationReserved<'a>),
    ReservationConsumed(fb::ReservationConsumed<'a>),
    ReservationReleased(fb::ReservationReleased<'a>),
    ReservationExpired(fb::ReservationExpired<'a>),
    CircuitOpened(fb::CircuitOpened<'a>),
    CircuitClosed(fb::CircuitClosed<'a>),
}

pub type RiskEventView<'a> = DecodedRiskEvent<'a>;

impl<'a> DecodedRiskEvent<'a> {
    pub fn kind(&self) -> RiskEventKind {
        <Self as BorrowedEventView<'a>>::kind(self)
    }

    pub fn metadata(&self) -> kairos_protocol::generated::kairos::common::v_2::EventMetadata<'a> {
        <Self as BorrowedEventView<'a>>::metadata(self)
    }
}

impl<'a> BorrowedEventView<'a> for DecodedRiskEvent<'a> {
    type Kind = RiskEventKind;

    fn kind(&self) -> Self::Kind {
        match self {
            Self::DecisionMade(_) => RiskEventKind::DecisionMade,
            Self::ReservationReserved(_) => RiskEventKind::ReservationReserved,
            Self::ReservationConsumed(_) => RiskEventKind::ReservationConsumed,
            Self::ReservationReleased(_) => RiskEventKind::ReservationReleased,
            Self::ReservationExpired(_) => RiskEventKind::ReservationExpired,
            Self::CircuitOpened(_) => RiskEventKind::CircuitOpened,
            Self::CircuitClosed(_) => RiskEventKind::CircuitClosed,
        }
    }

    fn metadata(&self) -> kairos_protocol::generated::kairos::common::v_2::EventMetadata<'a> {
        match self {
            Self::DecisionMade(value) => value.metadata(),
            Self::ReservationReserved(value) => value.metadata(),
            Self::ReservationConsumed(value) => value.metadata(),
            Self::ReservationReleased(value) => value.metadata(),
            Self::ReservationExpired(value) => value.metadata(),
            Self::CircuitOpened(value) => value.metadata(),
            Self::CircuitClosed(value) => value.metadata(),
        }
    }
}
