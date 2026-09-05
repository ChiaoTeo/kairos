use kairos_primitives::decimal::{Price, Quantity};
use kairos_primitives::market::Provider;
use kairos_primitives::reference::{InstrumentId, VenueId};
use kairos_primitives::time::UnixNanos;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Quote {
    pub scope: super::ObservationScope,
    pub instrument_id: InstrumentId,
    pub bid_price: Option<Price>,
    pub bid_quantity: Option<Quantity>,
    pub ask_price: Option<Price>,
    pub ask_quantity: Option<Quantity>,
    #[serde(default)]
    pub bid_venue_id: Option<VenueId>,
    #[serde(default)]
    pub ask_venue_id: Option<VenueId>,
    pub bid_venue_code: Option<String>,
    pub ask_venue_code: Option<String>,
    pub tape: Option<u32>,
    pub observed_at_unix_nanos: UnixNanos,
    pub provider: Provider,
}

#[cfg(test)]
mod tests {
    use super::Quote;

    #[test]
    fn replay_quote_without_canonical_venue_fields_preserves_raw_evidence() {
        let quote = Quote {
            scope: crate::ObservationScope::consolidated("instrument:fixture", None).unwrap(),
            instrument_id: kairos_primitives::reference::InstrumentId::new("instrument:fixture")
                .unwrap(),
            bid_price: None,
            bid_quantity: None,
            ask_price: None,
            ask_quantity: None,
            bid_venue_id: None,
            ask_venue_id: None,
            bid_venue_code: Some("19".into()),
            ask_venue_code: Some("11".into()),
            tape: Some(3),
            observed_at_unix_nanos: 7.into(),
            provider: kairos_primitives::market::Provider::new("massive").unwrap(),
        };
        let mut legacy = serde_json::to_value(&quote).unwrap();
        let fields = legacy.as_object_mut().unwrap();
        fields.remove("bid_venue_id");
        fields.remove("ask_venue_id");
        let restored: Quote = serde_json::from_value(legacy).unwrap();
        assert_eq!(restored, quote);
    }
}
