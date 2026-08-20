use std::collections::{BTreeMap, BTreeSet};

use crate::{IntegrationError, MarketEvent, MarketEventKind};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum SequenceDisposition {
    Accept,
    Duplicate,
}

#[derive(Default)]
pub(crate) struct OrderBookSequenceTracker {
    cursors: BTreeMap<String, u64>,
    requires_snapshot: BTreeSet<String>,
}

impl OrderBookSequenceTracker {
    pub(crate) fn clear(&mut self) {
        self.cursors.clear();
        self.requires_snapshot.clear();
    }

    pub(crate) fn seed(&mut self, symbol: &str, sequence: u64) {
        self.cursors.insert(symbol.to_owned(), sequence);
        self.requires_snapshot.remove(symbol);
    }

    /// Validate Binance's inclusive `[U, u]` diff range.
    ///
    /// A REST snapshot may be seeded before draining buffered diffs. Without a
    /// seed, the first diff establishes the stream-local cursor and subsequent
    /// gaps are still detected.
    pub(crate) fn validate_binance(
        &mut self,
        event: &MarketEvent,
    ) -> Result<SequenceDisposition, IntegrationError> {
        if event.kind != MarketEventKind::BookDelta {
            return Ok(SequenceDisposition::Accept);
        }
        let first = event.first_sequence.ok_or_else(|| {
            IntegrationError::InvalidPayload("Binance depth update is missing U".into())
        })?;
        let last = event.last_sequence.ok_or_else(|| {
            IntegrationError::InvalidPayload("Binance depth update is missing u".into())
        })?;
        let first = first.get();
        let last = last.get();
        if first > last {
            return Err(IntegrationError::InvalidPayload(
                "Binance depth update has an inverted sequence range".into(),
            ));
        }
        let key = event.symbol.to_string();
        if self.requires_snapshot.contains(&key) {
            return Err(IntegrationError::ResyncRequired(format!(
                "Binance {} depth requires a new REST snapshot",
                event.symbol
            )));
        }
        let Some(previous) = self.cursors.get(&key).copied() else {
            self.cursors.insert(key, last);
            return Ok(SequenceDisposition::Accept);
        };
        if last <= previous {
            return Ok(SequenceDisposition::Duplicate);
        }
        let expected = previous.saturating_add(1);
        if first > expected || last < expected {
            self.cursors.remove(&key);
            self.requires_snapshot.insert(key.clone());
            return Err(IntegrationError::ResyncRequired(format!(
                "Binance {} depth expected sequence {expected}, received [{first}, {last}]",
                event.symbol
            )));
        }
        self.cursors.insert(key, last);
        Ok(SequenceDisposition::Accept)
    }

    /// Validate OKX's `prevSeqId -> seqId` chain. A subscription snapshot is
    /// authoritative and establishes the cursor for later updates.
    pub(crate) fn validate_okx(
        &mut self,
        event: &MarketEvent,
    ) -> Result<SequenceDisposition, IntegrationError> {
        let key = event.symbol.to_string();
        if event.kind == MarketEventKind::BookSnapshot {
            let sequence = event.last_sequence.ok_or_else(|| {
                IntegrationError::InvalidPayload("OKX book snapshot is missing seqId".into())
            })?;
            self.cursors.insert(key, sequence.get());
            return Ok(SequenceDisposition::Accept);
        }
        if event.kind != MarketEventKind::BookDelta {
            return Ok(SequenceDisposition::Accept);
        }
        let previous = event.first_sequence.ok_or_else(|| {
            IntegrationError::InvalidPayload("OKX book update is missing prevSeqId".into())
        })?;
        let sequence = event.last_sequence.ok_or_else(|| {
            IntegrationError::InvalidPayload("OKX book update is missing seqId".into())
        })?;
        let Some(cursor) = self.cursors.get(&key).copied() else {
            return Err(IntegrationError::ResyncRequired(format!(
                "OKX {} book update arrived before a snapshot",
                event.symbol
            )));
        };
        if sequence.get() <= cursor {
            return Ok(SequenceDisposition::Duplicate);
        }
        if previous.get() != cursor {
            self.cursors.remove(&key);
            return Err(IntegrationError::ResyncRequired(format!(
                "OKX {} book expected prevSeqId {cursor}, received {}",
                event.symbol,
                previous.get()
            )));
        }
        self.cursors.insert(key, sequence.get());
        Ok(SequenceDisposition::Accept)
    }
}

#[cfg(test)]
mod tests {
    use kairos_primitives::integration::ParticipantSymbol;
    use kairos_primitives::time::UnixNanos;

    use super::*;

    fn book(kind: MarketEventKind, first: u64, last: u64) -> MarketEvent {
        MarketEvent {
            symbol: ParticipantSymbol::new("BTC-USDT").unwrap(),
            kind,
            price: None,
            quantity: None,
            rate: None,
            ask_price: None,
            ask_quantity: None,
            bids: Vec::new(),
            asks: Vec::new(),
            bar: None,
            greeks: None,
            first_sequence: Some(first.into()),
            last_sequence: Some(last.into()),
            sequence: Some(last.into()),
            observed_at_unix_nanos: UnixNanos::from(1),
            venue: Default::default(),
        }
    }

    #[test]
    fn binance_seed_bridges_snapshot_and_rejects_a_gap() {
        let mut tracker = OrderBookSequenceTracker::default();
        tracker.seed("BTC-USDT", 40);
        assert_eq!(
            tracker
                .validate_binance(&book(MarketEventKind::BookDelta, 39, 42))
                .unwrap(),
            SequenceDisposition::Accept
        );
        assert!(matches!(
            tracker.validate_binance(&book(MarketEventKind::BookDelta, 44, 45)),
            Err(IntegrationError::ResyncRequired(_))
        ));
        assert!(matches!(
            tracker.validate_binance(&book(MarketEventKind::BookDelta, 46, 47)),
            Err(IntegrationError::ResyncRequired(_))
        ));
        tracker.seed("BTC-USDT", 45);
        assert_eq!(
            tracker
                .validate_binance(&book(MarketEventKind::BookDelta, 46, 47))
                .unwrap(),
            SequenceDisposition::Accept
        );
    }

    #[test]
    fn okx_requires_snapshot_and_exact_prev_sequence() {
        let mut tracker = OrderBookSequenceTracker::default();
        assert!(matches!(
            tracker.validate_okx(&book(MarketEventKind::BookDelta, 40, 41)),
            Err(IntegrationError::ResyncRequired(_))
        ));
        tracker
            .validate_okx(&book(MarketEventKind::BookSnapshot, 0, 41))
            .unwrap();
        assert_eq!(
            tracker
                .validate_okx(&book(MarketEventKind::BookDelta, 41, 42))
                .unwrap(),
            SequenceDisposition::Accept
        );
        assert_eq!(
            tracker
                .validate_okx(&book(MarketEventKind::BookDelta, 41, 42))
                .unwrap(),
            SequenceDisposition::Duplicate
        );
    }
}
