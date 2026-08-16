use kairos_protocol::generated::kairos::risk::v_2 as fb;
pub enum DecodedRiskEvent<'a> {
    DecisionMade(fb::RiskDecisionMade<'a>),
    ReservationReserved(fb::ReservationReserved<'a>),
    ReservationConsumed(fb::ReservationConsumed<'a>),
    ReservationReleased(fb::ReservationReleased<'a>),
    ReservationExpired(fb::ReservationExpired<'a>),
    CircuitOpened(fb::CircuitOpened<'a>),
    CircuitClosed(fb::CircuitClosed<'a>),
}
