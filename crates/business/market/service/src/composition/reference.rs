use std::collections::VecDeque;

use crate::application::{ReferenceChangeSource, ReferenceEvent};

/// Concrete Reference event input for the Market process.
///
/// Reference event decoding and Aeron subscription are composition concerns.
/// The application receives only the sequence needed for reconciliation.
pub struct AeronReferenceChangeSource {
    subscription: kairos_transport::AeronByteSubscription,
    queue: VecDeque<u64>,
}

impl AeronReferenceChangeSource {
    pub fn connect(aeron_dir: Option<&str>, channel: &str, stream_id: i32) -> Result<Self, String> {
        let subscription =
            kairos_transport::AeronByteSubscription::connect(aeron_dir, channel, stream_id)
                .map_err(|error| error.to_string())?;
        Ok(Self {
            subscription,
            queue: VecDeque::new(),
        })
    }
}

impl ReferenceChangeSource for AeronReferenceChangeSource {
    fn next_event(&mut self) -> Result<Option<ReferenceEvent>, String> {
        while let Some(frame) = self
            .subscription
            .next_frame()
            .map_err(|error| error.to_string())?
        {
            let change = kairos_reference_contract::decode_event(&frame)
                .map_err(|error| error.to_string())?;
            self.queue.push_back(reference_event_sequence(&change));
        }

        Ok(self.queue.pop_front().map(|sequence| ReferenceEvent {
            sequence: sequence.into(),
        }))
    }
}

fn reference_event_sequence(event: &kairos_reference_contract::ReferenceEvent<'_>) -> u64 {
    use kairos_reference_contract::ReferenceEvent;

    match event {
        ReferenceEvent::EntityUpserted(value) => value.metadata().sequence(),
        ReferenceEvent::EntityUpdated(value) => value.metadata().sequence(),
        ReferenceEvent::FinancialProductUpserted(value) => value.metadata().sequence(),
        ReferenceEvent::FinancialProductUpdated(value) => value.metadata().sequence(),
        ReferenceEvent::AssetUpserted(value) => value.metadata().sequence(),
        ReferenceEvent::AssetUpdated(value) => value.metadata().sequence(),
        ReferenceEvent::ExchangeUpserted(value) => value.metadata().sequence(),
        ReferenceEvent::ExchangeUpdated(value) => value.metadata().sequence(),
        ReferenceEvent::ProviderUpserted(value) => value.metadata().sequence(),
        ReferenceEvent::ProviderUpdated(value) => value.metadata().sequence(),
        ReferenceEvent::BrokerUpserted(value) => value.metadata().sequence(),
        ReferenceEvent::BrokerUpdated(value) => value.metadata().sequence(),
        ReferenceEvent::ExecutionAccessUpserted(value) => value.metadata().sequence(),
        ReferenceEvent::ExecutionAccessUpdated(value) => value.metadata().sequence(),
        ReferenceEvent::MarketDataAccessUpserted(value) => value.metadata().sequence(),
        ReferenceEvent::MarketDataAccessUpdated(value) => value.metadata().sequence(),
        ReferenceEvent::InstrumentUpserted(value) => value.metadata().sequence(),
        ReferenceEvent::InstrumentUpdated(value) => value.metadata().sequence(),
        ReferenceEvent::ListingUpserted(value) => value.metadata().sequence(),
        ReferenceEvent::ListingUpdated(value) => value.metadata().sequence(),
        ReferenceEvent::MarketUpserted(value) => value.metadata().sequence(),
        ReferenceEvent::MarketUpdated(value) => value.metadata().sequence(),
    }
}
