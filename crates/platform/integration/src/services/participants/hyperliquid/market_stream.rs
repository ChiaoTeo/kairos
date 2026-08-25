//! Hyperliquid-owned planning and capacity policy for market WebSocket streams.

use std::collections::{BTreeMap, VecDeque};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Mutex, OnceLock};
use std::time::Duration;

use serde_json::{Value, json};

use crate::{IntegrationError, MarketDataKind, MarketFeed};

const PROVIDER_MAX_SUBSCRIPTIONS: usize = 1_000;
const RECOVERY_SUBSCRIPTION_RESERVE: usize = 50;
const PROVIDER_MESSAGES_PER_MINUTE: usize = 2_000;
const RECOVERY_MESSAGE_RESERVE: usize = 100;

static PROCESS_SUBSCRIPTIONS: AtomicUsize = AtomicUsize::new(0);
static PROCESS_MESSAGES: OnceLock<Mutex<VecDeque<std::time::Instant>>> = OnceLock::new();

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub(crate) enum PlannedStream {
    BestBidOffer(String),
    OrderBook(String),
    Trades(String),
    Candle { coin: String, interval: String },
    ActiveAssetContext(String),
}

impl PlannedStream {
    pub(crate) fn subscription(&self) -> Value {
        match self {
            Self::BestBidOffer(coin) => json!({"type":"bbo","coin":coin}),
            Self::OrderBook(coin) => json!({"type":"l2Book","coin":coin}),
            Self::Trades(coin) => json!({"type":"trades","coin":coin}),
            Self::Candle { coin, interval } => {
                json!({"type":"candle","coin":coin,"interval":interval})
            },
            Self::ActiveAssetContext(coin) => {
                json!({"type":"activeAssetCtx","coin":coin})
            },
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct MarketStreamPolicy {
    ordinary_subscription_limit: usize,
    ordinary_message_limit: usize,
    message_window: Duration,
}

impl Default for MarketStreamPolicy {
    fn default() -> Self {
        Self {
            ordinary_subscription_limit: PROVIDER_MAX_SUBSCRIPTIONS - RECOVERY_SUBSCRIPTION_RESERVE,
            ordinary_message_limit: PROVIDER_MESSAGES_PER_MINUTE - RECOVERY_MESSAGE_RESERVE,
            message_window: Duration::from_secs(60),
        }
    }
}

impl MarketStreamPolicy {
    pub(crate) fn plan(&self, feed: &MarketFeed) -> Result<PlannedStream, IntegrationError> {
        let coin = feed
            .symbol
            .as_ref()
            .ok_or_else(|| {
                IntegrationError::InvalidRequest("Hyperliquid market feed requires a coin".into())
            })?
            .as_str()
            .to_owned();
        match feed.kind {
            MarketDataKind::Quote => Ok(PlannedStream::BestBidOffer(coin)),
            MarketDataKind::OrderBook => Ok(PlannedStream::OrderBook(coin)),
            MarketDataKind::Trade => Ok(PlannedStream::Trades(coin)),
            MarketDataKind::Bar | MarketDataKind::TradeBar => Ok(PlannedStream::Candle {
                coin,
                interval: feed.interval.clone().unwrap_or_else(|| "1m".into()),
            }),
            MarketDataKind::MarkPrice
            | MarketDataKind::IndexPrice
            | MarketDataKind::FundingRate
            | MarketDataKind::OpenInterest => Ok(PlannedStream::ActiveAssetContext(coin)),
            MarketDataKind::QuoteBar
            | MarketDataKind::Ticker24h
            | MarketDataKind::Greeks
            | MarketDataKind::InstrumentStatus => Err(IntegrationError::UnsupportedOperation),
        }
    }

    pub(crate) fn admit_subscription_count(
        &self,
        desired: usize,
        recovery: bool,
    ) -> Result<(), IntegrationError> {
        let limit = if recovery {
            PROVIDER_MAX_SUBSCRIPTIONS
        } else {
            self.ordinary_subscription_limit
        };
        if desired > limit {
            return Err(IntegrationError::RateLimited(format!(
                "Hyperliquid WebSocket subscription capacity is {limit}; requested {desired}"
            )));
        }
        Ok(())
    }

    /// Reserves provider capacity shared by all Hyperliquid connections in
    /// this process. The upstream limit is per IP, so this prevents the common
    /// Spot + Perpetual topology from treating it as a per-socket allowance.
    pub(crate) fn reserve_process_subscriptions(
        &self,
        delta: usize,
        recovery: bool,
    ) -> Result<(), IntegrationError> {
        let limit = if recovery {
            PROVIDER_MAX_SUBSCRIPTIONS
        } else {
            self.ordinary_subscription_limit
        };
        let mut current = PROCESS_SUBSCRIPTIONS.load(Ordering::Acquire);
        loop {
            let desired = current.saturating_add(delta);
            if desired > limit {
                return Err(IntegrationError::RateLimited(format!(
                    "Hyperliquid process-wide WebSocket subscription capacity is {limit}; requested {desired}"
                )));
            }
            match PROCESS_SUBSCRIPTIONS.compare_exchange_weak(
                current,
                desired,
                Ordering::AcqRel,
                Ordering::Acquire,
            ) {
                Ok(_) => return Ok(()),
                Err(observed) => current = observed,
            }
        }
    }

    pub(crate) fn release_process_subscriptions(&self, count: usize) {
        let _ =
            PROCESS_SUBSCRIPTIONS.fetch_update(Ordering::AcqRel, Ordering::Acquire, |current| {
                Some(current.saturating_sub(count))
            });
    }
}

#[derive(Clone, Debug, Default)]
pub(crate) struct ControlBudget;

impl ControlBudget {
    pub(crate) fn admit(
        &mut self,
        policy: &MarketStreamPolicy,
        now: tokio::time::Instant,
        recovery: bool,
    ) -> Result<(), IntegrationError> {
        let sent_at = PROCESS_MESSAGES.get_or_init(|| Mutex::new(VecDeque::new()));
        let mut sent_at = sent_at.lock().map_err(|_| {
            IntegrationError::Unavailable("Hyperliquid control budget is poisoned".into())
        })?;
        let now = now.into_std();
        while sent_at
            .front()
            .is_some_and(|sent_at| *sent_at + policy.message_window <= now)
        {
            sent_at.pop_front();
        }
        let limit = if recovery {
            PROVIDER_MESSAGES_PER_MINUTE
        } else {
            policy.ordinary_message_limit
        };
        if sent_at.len() >= limit {
            return Err(IntegrationError::RateLimited(
                "Hyperliquid WebSocket message budget exhausted".into(),
            ));
        }
        sent_at.push_back(now);
        Ok(())
    }
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

pub(crate) fn event_is_demanded<'a>(
    feeds: impl IntoIterator<Item = &'a MarketFeed>,
    event: &crate::MarketEvent,
) -> bool {
    feeds.into_iter().any(|feed| {
        feed.symbol
            .as_ref()
            .is_some_and(|symbol| symbol.as_str().eq_ignore_ascii_case(event.symbol.as_str()))
            && matches!(
                (feed.kind, event.kind),
                (MarketDataKind::Quote, crate::MarketEventKind::Quote)
                    | (
                        MarketDataKind::OrderBook,
                        crate::MarketEventKind::BookSnapshot
                    )
                    | (MarketDataKind::Trade, crate::MarketEventKind::Trade)
                    | (MarketDataKind::Bar, crate::MarketEventKind::Bar)
                    | (MarketDataKind::TradeBar, crate::MarketEventKind::Bar)
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
            )
    })
}

#[cfg(test)]
mod tests {
    use kairos_primitives::integration::ParticipantSymbol;

    use super::*;

    fn feed(kind: MarketDataKind) -> MarketFeed {
        MarketFeed {
            kind,
            symbol: Some(ParticipantSymbol::new("BTC").unwrap()),
            interval: None,
            depth: None,
            update_speed_millis: None,
        }
    }

    #[test]
    fn quote_uses_symbol_scoped_bbo_instead_of_the_all_market_firehose() {
        assert_eq!(
            MarketStreamPolicy::default()
                .plan(&feed(MarketDataKind::Quote))
                .unwrap()
                .subscription(),
            json!({"type":"bbo","coin":"BTC"})
        );
    }

    #[test]
    fn context_observations_share_one_physical_subscription() {
        let policy = MarketStreamPolicy::default();
        let mark = policy.plan(&feed(MarketDataKind::MarkPrice)).unwrap();
        let funding = policy.plan(&feed(MarketDataKind::FundingRate)).unwrap();
        assert_eq!(mark, funding);
        assert_eq!(
            mark.subscription(),
            json!({"type":"activeAssetCtx","coin":"BTC"})
        );
    }

    #[test]
    fn ordinary_admission_preserves_subscription_recovery_capacity() {
        let policy = MarketStreamPolicy::default();
        policy
            .admit_subscription_count(PROVIDER_MAX_SUBSCRIPTIONS, true)
            .unwrap();
        assert!(matches!(
            policy.admit_subscription_count(PROVIDER_MAX_SUBSCRIPTIONS, false),
            Err(IntegrationError::RateLimited(_))
        ));
    }
}
