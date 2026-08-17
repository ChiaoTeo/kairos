use super::mmap::MmapMarketChangePublisher;
use crate::domain::events::MarketChange;

impl crate::application::MarketChangePublisher for MmapMarketChangePublisher {
    fn publish(&mut self, change: &MarketChange) -> Result<(), String> {
        Self::publish(self, change)
    }
}
