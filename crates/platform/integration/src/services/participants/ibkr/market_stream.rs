//! IBKR-owned planning, capacity and lifecycle for Level-I market data lines.

use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::pin::Pin;
use std::sync::Arc;
use std::task::{Context, Poll};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use futures_util::Stream;
use ibapi::contracts::Contract;
use ibapi::market_data::realtime::TickTypes;
use ibapi::subscriptions::{Subscription, SubscriptionItem};
use kairos_primitives::integration::ParticipantSymbol;

use super::execution::SessionService;
use super::market::{apply_tick, empty_quote};
use crate::{
    IntegrationError, MarketDataKind, MarketEvent, MarketEventKind, MarketQuote,
    MarketSubscriptionId, MarketSubscriptionRequest, MarketVenueEvidence,
};

const PROVIDER_MESSAGES_PER_SECOND: usize = 50;
const ORDINARY_MESSAGES_PER_SECOND: usize = 45;
const MESSAGE_WINDOW: Duration = Duration::from_secs(1);

struct PhysicalSubscription {
    upstream: Subscription<TickTypes>,
    references: usize,
    quote: MarketQuote,
}

pub(crate) struct MarketStreamService {
    session: Arc<SessionService>,
    exchange: String,
    currency: String,
    configured_line_limit: usize,
    ordinary_line_limit: usize,
    logical: BTreeMap<MarketSubscriptionId, Vec<ParticipantSymbol>>,
    physical: BTreeMap<ParticipantSymbol, PhysicalSubscription>,
    controls: VecDeque<tokio::time::Instant>,
    next_subscription_id: u64,
    poll_cursor: usize,
}

impl MarketStreamService {
    pub(crate) fn new(
        session: Arc<SessionService>,
        exchange: String,
        currency: String,
        configured_line_limit: usize,
    ) -> Result<Self, IntegrationError> {
        if configured_line_limit == 0 {
            return Err(IntegrationError::InvalidRequest(
                "IBKR market data line limit must be positive".into(),
            ));
        }
        let reserve = if configured_line_limit == 1 {
            0
        } else {
            (configured_line_limit / 10)
                .max(1)
                .min(configured_line_limit - 1)
        };
        Ok(Self {
            session,
            exchange,
            currency,
            configured_line_limit,
            ordinary_line_limit: configured_line_limit - reserve,
            logical: BTreeMap::new(),
            physical: BTreeMap::new(),
            controls: VecDeque::new(),
            next_subscription_id: 1,
            poll_cursor: 0,
        })
    }

    pub(crate) async fn subscribe(
        &mut self,
        request: &MarketSubscriptionRequest,
    ) -> Result<MarketSubscriptionId, IntegrationError> {
        let symbols = plan(request)?;
        let new_symbols = symbols
            .iter()
            .filter(|symbol| !self.physical.contains_key(*symbol))
            .cloned()
            .collect::<Vec<_>>();
        let desired = self.physical.len().saturating_add(new_symbols.len());
        if desired > self.ordinary_line_limit {
            return Err(IntegrationError::RateLimited(format!(
                "IBKR ordinary market data capacity is {} of {} configured lines; requested {desired}",
                self.ordinary_line_limit, self.configured_line_limit
            )));
        }
        self.admit_ordinary_controls(new_symbols.len())?;
        let client = self.session.client().await?;
        let mut created = Vec::new();
        for symbol in new_symbols {
            let contract = self.contract(&symbol);
            match client.market_data(&contract).streaming().subscribe().await {
                Ok(upstream) => created.push((symbol, upstream)),
                Err(error) => {
                    for (_, subscription) in created {
                        self.wait_for_recovery_control().await;
                        subscription.cancel().await;
                    }
                    return Err(classify_error(&error.to_string()));
                },
            }
        }
        for (symbol, upstream) in created {
            self.physical.insert(
                symbol.clone(),
                PhysicalSubscription {
                    upstream,
                    references: 0,
                    quote: empty_quote(symbol),
                },
            );
        }
        for symbol in &symbols {
            self.physical
                .get_mut(symbol)
                .expect("planned IBKR stream exists")
                .references += 1;
        }
        let id = MarketSubscriptionId(self.next_subscription_id);
        self.next_subscription_id = self.next_subscription_id.saturating_add(1);
        self.logical.insert(id, symbols);
        Ok(id)
    }

    pub(crate) async fn unsubscribe(
        &mut self,
        id: MarketSubscriptionId,
    ) -> Result<(), IntegrationError> {
        let symbols = self.logical.get(&id).cloned().ok_or_else(|| {
            IntegrationError::InvalidRequest("unknown IBKR market subscription".into())
        })?;
        let removed = symbols
            .iter()
            .filter(|symbol| {
                self.physical
                    .get(*symbol)
                    .is_some_and(|physical| physical.references == 1)
            })
            .count();
        self.admit_ordinary_controls(removed)?;
        self.logical.remove(&id);
        for symbol in symbols {
            let physical = self
                .physical
                .get_mut(&symbol)
                .expect("logical IBKR stream has physical subscription");
            physical.references -= 1;
            if physical.references == 0 {
                let physical = self.physical.remove(&symbol).unwrap();
                physical.upstream.cancel().await;
            }
        }
        Ok(())
    }

    pub(crate) async fn suspend(&mut self) {
        let subscriptions = std::mem::take(&mut self.physical);
        for (_, physical) in subscriptions {
            self.wait_for_recovery_control().await;
            physical.upstream.cancel().await;
        }
        self.poll_cursor = 0;
    }

    pub(crate) async fn restore(&mut self) -> Result<(), IntegrationError> {
        let counts = reference_counts(self.logical.values());
        if counts.len() > self.configured_line_limit {
            return Err(IntegrationError::RateLimited(format!(
                "IBKR recovery needs {} market data lines but only {} are configured",
                counts.len(),
                self.configured_line_limit
            )));
        }
        let client = self.session.client().await?;
        let mut restored = BTreeMap::new();
        for (symbol, references) in counts {
            self.wait_for_recovery_control().await;
            let contract = self.contract(&symbol);
            match client.market_data(&contract).streaming().subscribe().await {
                Ok(upstream) => {
                    restored.insert(
                        symbol.clone(),
                        PhysicalSubscription {
                            upstream,
                            references,
                            quote: empty_quote(symbol),
                        },
                    );
                },
                Err(error) => {
                    for (_, physical) in restored {
                        self.wait_for_recovery_control().await;
                        physical.upstream.cancel().await;
                    }
                    return Err(classify_error(&error.to_string()));
                },
            }
        }
        self.physical = restored;
        self.poll_cursor = 0;
        Ok(())
    }

    pub(crate) fn poll_next(
        &mut self,
        cx: &mut Context<'_>,
    ) -> Poll<Result<MarketEvent, IntegrationError>> {
        let symbols = self.physical.keys().cloned().collect::<Vec<_>>();
        if symbols.is_empty() {
            return Poll::Pending;
        }
        for offset in 0..symbols.len() {
            let index = (self.poll_cursor + offset) % symbols.len();
            let symbol = &symbols[index];
            let physical = self.physical.get_mut(symbol).expect("IBKR stream exists");
            match Pin::new(&mut physical.upstream).poll_next(cx) {
                Poll::Ready(Some(Ok(SubscriptionItem::Data(tick)))) => {
                    self.poll_cursor = (index + 1) % symbols.len();
                    if apply_tick(&mut physical.quote, &tick)? {
                        physical.quote.observed_at_unix_nanos = now();
                        return Poll::Ready(Ok(quote_event(&physical.quote)));
                    }
                    cx.waker().wake_by_ref();
                },
                Poll::Ready(Some(Ok(SubscriptionItem::Notice(notice)))) => {
                    self.poll_cursor = (index + 1) % symbols.len();
                    if let Some(error) = classify_notice(&notice.to_string()) {
                        return Poll::Ready(Err(error));
                    }
                    cx.waker().wake_by_ref();
                },
                Poll::Ready(Some(Err(error))) => {
                    return Poll::Ready(Err(classify_error(&error.to_string())));
                },
                Poll::Ready(None) => {
                    return Poll::Ready(Err(IntegrationError::ResyncRequired(format!(
                        "IBKR market data stream ended for {symbol}"
                    ))));
                },
                Poll::Pending => {},
            }
        }
        Poll::Pending
    }

    fn contract(&self, symbol: &ParticipantSymbol) -> Contract {
        Contract::stock(symbol.as_str())
            .on_exchange(&self.exchange)
            .in_currency(&self.currency)
            .build()
    }

    fn prune_controls(&mut self, now: tokio::time::Instant) {
        while self
            .controls
            .front()
            .is_some_and(|sent| *sent + MESSAGE_WINDOW <= now)
        {
            self.controls.pop_front();
        }
    }

    fn admit_ordinary_controls(&mut self, count: usize) -> Result<(), IntegrationError> {
        let now = tokio::time::Instant::now();
        self.prune_controls(now);
        if self.controls.len().saturating_add(count) > ORDINARY_MESSAGES_PER_SECOND {
            return Err(IntegrationError::RateLimited(format!(
                "IBKR ordinary control budget is {ORDINARY_MESSAGES_PER_SECOND} messages per second"
            )));
        }
        self.controls.extend(std::iter::repeat_n(now, count));
        Ok(())
    }

    async fn wait_for_recovery_control(&mut self) {
        loop {
            let now = tokio::time::Instant::now();
            self.prune_controls(now);
            if self.controls.len() < PROVIDER_MESSAGES_PER_SECOND {
                self.controls.push_back(now);
                return;
            }
            let deadline = self.controls.front().copied().unwrap() + MESSAGE_WINDOW;
            tokio::time::sleep_until(deadline).await;
        }
    }
}

fn reference_counts<'a>(
    subscriptions: impl IntoIterator<Item = &'a Vec<ParticipantSymbol>>,
) -> BTreeMap<ParticipantSymbol, usize> {
    let mut counts = BTreeMap::new();
    for symbols in subscriptions {
        for symbol in symbols {
            *counts.entry(symbol.clone()).or_default() += 1;
        }
    }
    counts
}

fn plan(request: &MarketSubscriptionRequest) -> Result<Vec<ParticipantSymbol>, IntegrationError> {
    let mut symbols = BTreeSet::new();
    for feed in &request.feeds {
        if feed.kind != MarketDataKind::Quote
            || feed.interval.is_some()
            || feed.depth.is_some()
            || feed.update_speed_millis.is_some()
        {
            return Err(IntegrationError::UnsupportedOperation);
        }
        symbols.insert(feed.symbol.clone().ok_or_else(|| {
            IntegrationError::InvalidRequest("IBKR quote feed requires a symbol".into())
        })?);
    }
    Ok(symbols.into_iter().collect())
}

fn quote_event(quote: &MarketQuote) -> MarketEvent {
    MarketEvent {
        symbol: quote.symbol.clone(),
        kind: MarketEventKind::Quote,
        price: quote.bid_price.or(quote.last_price),
        quantity: quote.bid_quantity,
        rate: None,
        ask_price: quote.ask_price,
        ask_quantity: quote.ask_quantity,
        bids: Vec::new(),
        asks: Vec::new(),
        bar: None,
        greeks: None,
        first_sequence: None,
        last_sequence: None,
        sequence: None,
        observed_at_unix_nanos: quote.observed_at_unix_nanos,
        venue: MarketVenueEvidence::default(),
    }
}

fn classify_notice(message: &str) -> Option<IntegrationError> {
    let lower = message.to_ascii_lowercase();
    if lower.contains("not subscribed")
        || lower.contains("market data subscription")
        || lower.contains("no market data permissions")
        || lower.contains("error 354")
    {
        Some(IntegrationError::Entitlement(message.into()))
    } else if lower.contains("max rate")
        || lower.contains("maximum number of tickers")
        || lower.contains("pacing violation")
        || lower.contains("error 100")
        || lower.contains("error 101")
        || lower.contains("error 420")
    {
        Some(IntegrationError::RateLimited(message.into()))
    } else {
        None
    }
}

fn classify_error(message: &str) -> IntegrationError {
    classify_notice(message).unwrap_or_else(|| IntegrationError::Transport(message.into()))
}

fn now() -> kairos_primitives::time::UnixNanos {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_nanos())
        .unwrap_or_default();
    u64::try_from(nanos).unwrap_or(u64::MAX).into()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::MarketFeed;

    fn feed(kind: MarketDataKind) -> MarketFeed {
        MarketFeed {
            kind,
            symbol: Some(ParticipantSymbol::new("AAPL").unwrap()),
            interval: None,
            depth: None,
            update_speed_millis: None,
        }
    }

    #[test]
    fn only_level_one_quotes_enter_the_current_ibkr_plan() {
        assert_eq!(
            plan(&MarketSubscriptionRequest::new(vec![feed(MarketDataKind::Quote)]).unwrap())
                .unwrap(),
            vec![ParticipantSymbol::new("AAPL").unwrap()]
        );
        assert!(matches!(
            plan(&MarketSubscriptionRequest::new(vec![feed(MarketDataKind::OrderBook)]).unwrap()),
            Err(IntegrationError::UnsupportedOperation)
        ));
    }

    #[test]
    fn tws_entitlement_and_pacing_errors_keep_their_semantics() {
        assert!(matches!(
            classify_error("Error 354: Requested market data is not subscribed"),
            IntegrationError::Entitlement(_)
        ));
        assert!(matches!(
            classify_error("Error 101: Max number of tickers has been reached"),
            IntegrationError::RateLimited(_)
        ));
    }

    #[test]
    fn recovery_plan_deduplicates_shared_strategy_demand() {
        let symbol = ParticipantSymbol::new("AAPL").unwrap();
        let logical = [vec![symbol.clone()], vec![symbol.clone()]];
        assert_eq!(reference_counts(logical.iter()).get(&symbol), Some(&2));
        assert_eq!(reference_counts(logical.iter()).len(), 1);
    }
}
