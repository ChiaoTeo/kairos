//! Stable connection vocabulary. Provider SDK types must not appear here.

pub mod account;
pub mod bindings;
pub mod capabilities;
pub mod connections;
pub mod market;
pub mod order;
pub mod products;
pub mod reference;
pub mod spec;

pub use account::{
    ExternalAccountEvent, ExternalAccountModel, ExternalAccountSegment, ExternalAccountSnapshot,
    ExternalAccountStatus, ExternalBalance, ExternalDecimal, ExternalFillEvent, ExternalMarginMode,
    ExternalOpenOrder, ExternalOrderEvent, ExternalOrderStatus, ExternalPosition,
    ExternalPositionMode,
};
pub use bindings::{AccessScope, AssetType, TransportKind};
pub use capabilities::IntegrationCapability;
pub use connections::{ConnectionHealth, ConnectionIdentity, ConnectionLifecycle, ConnectionState};
pub use market::{MarketEvent, MarketEventKind, MarketQuote, MarketTrade};
pub use order::{
    DecimalValue, ExecutionReport, Order, OrderEntryEvent, OrderEntryOptions, OrderEntryRequest,
    OrderEntryStatus, OrderRequest, OrderSide, OrderStatus, OrderType, TimeInForce,
};
pub use products::ProductFamily;
pub use reference::{
    ReferenceAsset, ReferenceCatalogPayload, ReferenceEntity, ReferenceInstrument,
    ReferenceListing, ReferenceMarket,
};
pub use spec::ConnectionSpec;
