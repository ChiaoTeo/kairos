mod bar;
mod freshness;
mod funding_rate;
mod greeks;
mod index_price;
mod key;
mod mark_price;
mod metadata;
mod open_interest;
mod order_book;
mod quote;
mod rate;
mod ticker_24h;

pub use bar::BarWindowView;
pub use freshness::FreshnessView;
pub use funding_rate::FundingRateLatestView;
pub use greeks::GreeksLatestView;
pub use index_price::IndexPriceLatestView;
pub use key::{MarketViewKey, MarketViewKind};
pub use mark_price::MarkPriceLatestView;
pub use metadata::ViewMetadata;
pub use open_interest::OpenInterestLatestView;
pub use order_book::OrderBookLatestView;
pub use quote::QuoteLatestView;
pub use rate::RateLatestView;
pub use ticker_24h::Ticker24hLatestView;

use std::path::Path;

use kairos_transport::SharedSnapshotWriter;

use crate::{ContractError, ContractResult};

pub struct ViewFrame {
    generation: u64,
    bytes: Vec<u8>,
}

impl ViewFrame {
    pub(crate) fn new(generation: u64, bytes: Vec<u8>) -> Self {
        Self { generation, bytes }
    }

    pub fn generation(&self) -> u64 {
        self.generation
    }

    pub fn bytes(&self) -> &[u8] {
        &self.bytes
    }

    pub fn quote(&self) -> ContractResult<QuoteLatestView<'_>> {
        quote::decode(self.bytes())
    }

    pub fn bar(&self) -> ContractResult<BarWindowView<'_>> {
        bar::decode(self.bytes())
    }

    pub fn greeks(&self) -> ContractResult<GreeksLatestView<'_>> {
        greeks::decode(self.bytes())
    }

    pub fn order_book(&self) -> ContractResult<OrderBookLatestView<'_>> {
        order_book::decode(self.bytes())
    }

    pub fn freshness(&self) -> ContractResult<FreshnessView<'_>> {
        freshness::decode(self.bytes())
    }

    pub fn rate(&self) -> ContractResult<RateLatestView<'_>> {
        rate::decode(self.bytes())
    }

    pub fn ticker_24h(&self) -> ContractResult<Ticker24hLatestView<'_>> {
        ticker_24h::decode(self.bytes())
    }

    pub fn mark_price(&self) -> ContractResult<MarkPriceLatestView<'_>> {
        mark_price::decode(self.bytes())
    }

    pub fn funding_rate(&self) -> ContractResult<FundingRateLatestView<'_>> {
        funding_rate::decode(self.bytes())
    }

    pub fn open_interest(&self) -> ContractResult<OpenInterestLatestView<'_>> {
        open_interest::decode(self.bytes())
    }

    pub fn index_price(&self) -> ContractResult<IndexPriceLatestView<'_>> {
        index_price::decode(self.bytes())
    }
}

pub struct MarketViewReader {
    key: MarketViewKey,
    reader: crate::transport::MarketMmapReader,
}

impl MarketViewReader {
    pub fn open(root: impl AsRef<Path>, key: MarketViewKey) -> ContractResult<Self> {
        Ok(Self {
            key: key.clone(),
            reader: crate::transport::MarketMmapReader::open(root, key)?,
        })
    }

    pub fn key(&self) -> &MarketViewKey {
        &self.key
    }

    pub fn read(&self) -> ContractResult<ViewFrame> {
        self.reader.read()
    }
}

pub struct MarketViewPublisher {
    key: MarketViewKey,
    writer: SharedSnapshotWriter,
}

impl MarketViewPublisher {
    pub fn create(
        root: impl AsRef<Path>,
        key: MarketViewKey,
        slot_size: usize,
    ) -> ContractResult<Self> {
        let writer = SharedSnapshotWriter::create(key.resource_path(root), slot_size)
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        Ok(Self { key, writer })
    }

    pub fn key(&self) -> &MarketViewKey {
        &self.key
    }

    pub fn publish(&mut self, generation: u64, payload: &[u8]) -> ContractResult<()> {
        self.writer
            .publish(generation, payload)
            .map_err(|error| ContractError::Transport(error.to_string()))
    }
}
