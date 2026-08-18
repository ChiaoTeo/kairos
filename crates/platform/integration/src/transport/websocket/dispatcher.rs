//! Correlates command acknowledgements without losing interleaved push data.

use std::collections::VecDeque;

use crate::IntegrationError;

pub(crate) struct InboundDispatcher<E> {
    buffered: VecDeque<E>,
    capacity: usize,
}

impl<E> InboundDispatcher<E> {
    pub(crate) fn new(capacity: usize) -> Result<Self, IntegrationError> {
        if capacity == 0 {
            return Err(IntegrationError::InvalidRequest(
                "WebSocket dispatcher capacity must be positive".into(),
            ));
        }
        Ok(Self {
            buffered: VecDeque::with_capacity(capacity.min(1024)),
            capacity,
        })
    }

    pub(crate) fn buffer(&mut self, event: E) -> Result<(), IntegrationError> {
        if self.buffered.len() == self.capacity {
            return Err(IntegrationError::Backpressure(
                "WebSocket event buffer filled while awaiting an acknowledgement".into(),
            ));
        }
        self.buffered.push_back(event);
        Ok(())
    }

    pub(crate) fn extend(
        &mut self,
        events: impl IntoIterator<Item = E>,
    ) -> Result<(), IntegrationError> {
        for event in events {
            self.buffer(event)?;
        }
        Ok(())
    }

    pub(crate) fn pop(&mut self) -> Option<E> {
        self.buffered.pop_front()
    }

    pub(crate) fn clear(&mut self) {
        self.buffered.clear();
    }

    #[cfg(test)]
    fn len(&self) -> usize {
        self.buffered.len()
    }
}

#[cfg(test)]
mod tests {
    use super::InboundDispatcher;

    #[test]
    fn preserves_events_that_arrive_before_an_acknowledgement() {
        let mut dispatcher = InboundDispatcher::new(2).unwrap();
        dispatcher.buffer("quote-1").unwrap();
        dispatcher.buffer("trade-2").unwrap();

        assert_eq!(dispatcher.len(), 2);
        assert_eq!(dispatcher.pop(), Some("quote-1"));
        assert_eq!(dispatcher.pop(), Some("trade-2"));
    }

    #[test]
    fn reports_backpressure_instead_of_dropping_interleaved_events() {
        let mut dispatcher = InboundDispatcher::new(1).unwrap();
        dispatcher.buffer("quote-1").unwrap();
        assert!(dispatcher.buffer("quote-2").is_err());
    }
}
