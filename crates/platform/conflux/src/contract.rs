/// The REST request/response pair exposed by one process Contract.
///
/// Transport, routing, serialization and client construction remain owned by
/// the module's Contract crate. Conflux only records the closed type pair.
pub trait RestContract: Send + 'static {
    type Request: Send + 'static;
    type Response: Send + 'static;
}

/// The one outward Contract implemented by a Conflux Actor.
///
/// View and Aeron capabilities intentionally remain on each module's concrete
/// Contract client until the existing modules establish a real shared shape.
pub trait Contract: Send + 'static {
    type Rest: RestContract;
}

pub type RestRequestOf<C> = <<C as Contract>::Rest as RestContract>::Request;
pub type RestResponseOf<C> = <<C as Contract>::Rest as RestContract>::Response;
