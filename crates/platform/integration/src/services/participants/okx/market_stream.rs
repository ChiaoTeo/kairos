//! OKX-owned planning and control policy for public market WebSocket streams.

use std::collections::{BTreeMap, VecDeque};
use std::time::Duration;

use serde_json::{Value, json};

use crate::{IntegrationError, MarketDataKind, MarketFeed};

/// OKX limits subscribe/unsubscribe/login operations to 480 per connection per
/// hour. Keep a fixed reserve for recovery instead of consuming the provider
/// ceiling during ordinary churn.
const PROVIDER_CONTROL_OPERATIONS_PER_HOUR: usize = 480;
const RECOVERY_OPERATION_RESERVE: usize = 24;
const PROVIDER_CONTROL_PAYLOAD_BYTES: usize = 64 * 1024;
const CONTROL_PAYLOAD_RESERVE_BYTES: usize = 4 * 1024;

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub(crate) enum StreamSelector {
    Instrument(String),
    InstrumentFamily(String),
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub(crate) struct PlannedStream {
    pub(crate) channel: String,
    pub(crate) selector: StreamSelector,
}

impl PlannedStream {
    pub(crate) fn argument(&self) -> Value {
        match &self.selector {
            StreamSelector::Instrument(instrument) => {
                json!({"channel": self.channel, "instId": instrument})
            },
            StreamSelector::InstrumentFamily(family) => {
                json!({"channel": self.channel, "instFamily": family})
            },
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct ControlBatch {
    pub(crate) streams: Vec<PlannedStream>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct MarketStreamPolicy {
    control_window: Duration,
    ordinary_control_limit: usize,
    max_control_payload_bytes: usize,
}

impl Default for MarketStreamPolicy {
    fn default() -> Self {
        Self {
            control_window: Duration::from_secs(60 * 60),
            ordinary_control_limit: PROVIDER_CONTROL_OPERATIONS_PER_HOUR
                - RECOVERY_OPERATION_RESERVE,
            max_control_payload_bytes: PROVIDER_CONTROL_PAYLOAD_BYTES
                - CONTROL_PAYLOAD_RESERVE_BYTES,
        }
    }
}

impl MarketStreamPolicy {
    pub(crate) fn plan(&self, feed: &MarketFeed) -> Result<PlannedStream, IntegrationError> {
        plan(feed)
    }

    pub(crate) fn batches(
        &self,
        operation: &str,
        streams: impl IntoIterator<Item = PlannedStream>,
    ) -> Result<Vec<ControlBatch>, IntegrationError> {
        let mut batches = Vec::new();
        let mut current = Vec::new();
        for stream in streams {
            let mut candidate = current.clone();
            candidate.push(stream.clone());
            let arguments = candidate
                .iter()
                .map(PlannedStream::argument)
                .collect::<Vec<_>>();
            let payload = json!({"id":"18446744073709551615","op":operation,"args":arguments});
            let bytes = serde_json::to_vec(&payload)
                .map_err(|error| IntegrationError::InvalidPayload(error.to_string()))?
                .len();
            if bytes > self.max_control_payload_bytes {
                if current.is_empty() {
                    return Err(IntegrationError::InvalidRequest(
                        "one OKX subscription argument exceeds the safe control payload size"
                            .into(),
                    ));
                }
                batches.push(ControlBatch { streams: current });
                current = vec![stream];
            } else {
                current = candidate;
            }
        }
        if !current.is_empty() {
            batches.push(ControlBatch { streams: current });
        }
        Ok(batches)
    }

    pub(crate) fn ordinary_control_limit(&self) -> usize {
        self.ordinary_control_limit
    }

    pub(crate) fn control_window(&self) -> Duration {
        self.control_window
    }

    #[cfg(test)]
    pub(crate) fn max_control_payload_bytes(&self) -> usize {
        self.max_control_payload_bytes
    }
}

#[derive(Clone, Debug, Default)]
pub(crate) struct ControlBudget {
    sent_at: VecDeque<tokio::time::Instant>,
}

impl ControlBudget {
    pub(crate) fn admit(
        &mut self,
        policy: &MarketStreamPolicy,
        now: tokio::time::Instant,
        recovery: bool,
    ) -> Result<(), IntegrationError> {
        while self
            .sent_at
            .front()
            .is_some_and(|sent_at| *sent_at + policy.control_window() <= now)
        {
            self.sent_at.pop_front();
        }
        let limit = if recovery {
            PROVIDER_CONTROL_OPERATIONS_PER_HOUR
        } else {
            policy.ordinary_control_limit()
        };
        if self.sent_at.len() >= limit {
            let retry_at = self
                .sent_at
                .front()
                .map(|sent_at| *sent_at + policy.control_window())
                .unwrap_or(now);
            return Err(IntegrationError::RateLimited(format!(
                "OKX WebSocket control budget exhausted; retry after {:?}",
                retry_at.saturating_duration_since(now)
            )));
        }
        self.sent_at.push_back(now);
        Ok(())
    }

    pub(crate) fn reset(&mut self) {
        self.sent_at.clear();
    }
}

pub(crate) fn event_is_demanded<'a>(
    feeds: impl IntoIterator<Item = &'a MarketFeed>,
    event: &crate::MarketEvent,
) -> bool {
    feeds.into_iter().any(|feed| {
        let symbol_matches = feed
            .symbol
            .as_ref()
            .is_some_and(|symbol| symbol.as_str().eq_ignore_ascii_case(event.symbol.as_str()));
        symbol_matches
            && matches!(
                (feed.kind, event.kind),
                (MarketDataKind::Quote, crate::MarketEventKind::Quote)
                    | (MarketDataKind::Ticker24h, crate::MarketEventKind::Ticker24h)
                    | (MarketDataKind::Trade, crate::MarketEventKind::Trade)
                    | (
                        MarketDataKind::OrderBook,
                        crate::MarketEventKind::BookSnapshot
                    )
                    | (MarketDataKind::OrderBook, crate::MarketEventKind::BookDelta)
                    | (MarketDataKind::Bar, crate::MarketEventKind::Bar)
                    | (MarketDataKind::TradeBar, crate::MarketEventKind::Bar)
                    | (MarketDataKind::QuoteBar, crate::MarketEventKind::Bar)
                    | (MarketDataKind::MarkPrice, crate::MarketEventKind::MarkPrice)
                    | (
                        MarketDataKind::IndexPrice,
                        crate::MarketEventKind::IndexPrice
                    )
                    | (
                        MarketDataKind::FundingRate,
                        crate::MarketEventKind::FundingRate
                    )
                    | (
                        MarketDataKind::OpenInterest,
                        crate::MarketEventKind::OpenInterest
                    )
                    | (MarketDataKind::Greeks, crate::MarketEventKind::Greeks)
                    | (
                        MarketDataKind::InstrumentStatus,
                        crate::MarketEventKind::InstrumentStatus
                    )
            )
    })
}

pub(crate) fn reference_counts(
    subscriptions: impl IntoIterator<Item = Vec<PlannedStream>>,
) -> BTreeMap<PlannedStream, usize> {
    let mut result = BTreeMap::new();
    for streams in subscriptions {
        for stream in streams {
            *result.entry(stream).or_default() += 1;
        }
    }
    result
}

fn plan(feed: &MarketFeed) -> Result<PlannedStream, IntegrationError> {
    if feed.kind == MarketDataKind::InstrumentStatus {
        return Err(IntegrationError::UnsupportedOperation);
    }
    let symbol = feed.symbol.as_ref().ok_or_else(|| {
        IntegrationError::InvalidRequest(format!("OKX {:?} feed requires a symbol", feed.kind))
    })?;
    let channel = match feed.kind {
        MarketDataKind::Quote | MarketDataKind::Ticker24h => "tickers".into(),
        MarketDataKind::Trade => "trades".into(),
        MarketDataKind::OrderBook => match feed.depth {
            Some(depth) if depth <= 5 => "books5".into(),
            _ => "books".into(),
        },
        MarketDataKind::Bar | MarketDataKind::TradeBar | MarketDataKind::QuoteBar => {
            format!("candle{}", feed.interval.as_deref().unwrap_or("1m"))
        },
        MarketDataKind::MarkPrice => "mark-price".into(),
        MarketDataKind::IndexPrice => "index-tickers".into(),
        MarketDataKind::FundingRate => "funding-rate".into(),
        MarketDataKind::OpenInterest => "open-interest".into(),
        MarketDataKind::Greeks => "opt-summary".into(),
        MarketDataKind::InstrumentStatus => unreachable!("rejected above"),
    };
    let selector = if feed.kind == MarketDataKind::Greeks {
        StreamSelector::InstrumentFamily(option_family(symbol.as_str())?)
    } else {
        StreamSelector::Instrument(symbol.as_str().into())
    };
    Ok(PlannedStream { channel, selector })
}

fn option_family(symbol: &str) -> Result<String, IntegrationError> {
    let mut parts = symbol.split('-');
    let base = parts.next().unwrap_or_default();
    let quote = parts.next().unwrap_or_default();
    if base.is_empty() || quote.is_empty() || parts.next().is_none() {
        return Err(IntegrationError::InvalidRequest(format!(
            "OKX option symbol `{symbol}` does not contain an instrument family"
        )));
    }
    Ok(format!("{base}-{quote}"))
}

#[cfg(test)]
mod tests {
    use kairos_primitives::integration::ParticipantSymbol;

    use super::*;

    fn feed(kind: MarketDataKind, symbol: &str) -> MarketFeed {
        MarketFeed {
            kind,
            symbol: Some(ParticipantSymbol::new(symbol).unwrap()),
            interval: None,
            depth: None,
            update_speed_millis: None,
        }
    }

    #[test]
    fn option_greeks_use_the_required_instrument_family_selector() {
        let planned = MarketStreamPolicy::default()
            .plan(&feed(MarketDataKind::Greeks, "BTC-USD-260925-100000-C"))
            .unwrap();

        assert_eq!(
            planned.argument(),
            json!({"channel":"opt-summary","instFamily":"BTC-USD"})
        );
    }

    #[test]
    fn system_maintenance_is_not_misreported_as_instrument_status() {
        assert!(matches!(
            MarketStreamPolicy::default().plan(&feed(MarketDataKind::InstrumentStatus, "BTC-USDT")),
            Err(IntegrationError::UnsupportedOperation)
        ));
    }

    #[test]
    fn ordinary_control_budget_preserves_recovery_reserve() {
        let policy = MarketStreamPolicy::default();
        let now = tokio::time::Instant::now();
        let mut budget = ControlBudget::default();
        for _ in 0..policy.ordinary_control_limit() {
            budget.admit(&policy, now, false).unwrap();
        }
        assert!(matches!(
            budget.admit(&policy, now, false),
            Err(IntegrationError::RateLimited(_))
        ));
        for _ in 0..RECOVERY_OPERATION_RESERVE {
            budget.admit(&policy, now, true).unwrap();
        }
        assert!(matches!(
            budget.admit(&policy, now, true),
            Err(IntegrationError::RateLimited(_))
        ));
    }

    #[test]
    fn control_batches_stay_below_the_safe_payload_limit() {
        let policy = MarketStreamPolicy::default();
        let streams = (0..2_000)
            .map(|index| PlannedStream {
                channel: "tickers".into(),
                selector: StreamSelector::Instrument(format!("ASSET-{index}-USDT")),
            })
            .collect::<Vec<_>>();
        let batches = policy.batches("subscribe", streams.clone()).unwrap();
        assert!(batches.len() > 1);
        assert_eq!(
            batches
                .iter()
                .map(|batch| batch.streams.len())
                .sum::<usize>(),
            streams.len()
        );
        for batch in batches {
            let bytes = serde_json::to_vec(&json!({
                "id":"18446744073709551615",
                "op":"subscribe",
                "args":batch.streams.iter().map(PlannedStream::argument).collect::<Vec<_>>()
            }))
            .unwrap()
            .len();
            assert!(bytes <= policy.max_control_payload_bytes());
        }
    }
}
