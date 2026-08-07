use crate::domain::market::MarketDescriptor;

/// Notification that a newer Reference snapshot is available.
///
/// The notification is deliberately a watermark plus affected data. The
/// Reference actor remains the owner of the catalog; Market only consumes the
/// change and reconciles its own subscription state.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReferenceChanged {
    pub generation: u64,
    pub event_sequence: u64,
    pub markets: Vec<MarketDescriptor>,
}
