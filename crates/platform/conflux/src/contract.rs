/// Client-side REST command/query plane of a module Contract.
pub trait RestContract: Send + 'static {
    type Client: Send + 'static;
}

/// Aeron stream plane of a module Contract.
pub trait AeronContract: Send + 'static {
    type Frame: Send + 'static;
    type Stream: Send + 'static;
}

/// Client-side current-view plane of a module Contract.
pub trait ViewContract: Send + 'static {
    type Key: Send + Sync + 'static;
    type Frame: Send + 'static;
    type Reader: Send + 'static;
}

/// A module-owned client-side executable cross-process protocol.
///
/// This shape follows the existing Account, Market, Execution, Reference and
/// Risk Contract crates: one endpoint creates one unified client facade, while
/// REST, views and Aeron streams remain distinct capabilities.
pub trait Contract: Send + 'static {
    type Endpoint: Send + 'static;
    type Client: Send + 'static;
    type Rest: RestContract;
    type View: ViewContract;
    type Aeron: AeronContract;
}

/// A Contract whose complete server boundary has moved into its Contract
/// crate and can therefore be hosted by Conflux without raw route dispatch.
pub trait ServedContract: Contract {
    /// Closed REST call enum accepted by the owning Actor.
    type RestCall: Send + 'static;
    /// Contract-owned bundle of REST host, view publisher and Aeron publisher.
    type Service: Send + 'static;
}

pub type RestCallOf<C> = <C as ServedContract>::RestCall;

/// Explicit empty Aeron plane.
pub struct NoAeron;

impl AeronContract for NoAeron {
    type Frame = ();
    type Stream = ();
}

/// Explicit empty view plane.
pub struct NoViews;

impl ViewContract for NoViews {
    type Key = ();
    type Frame = ();
    type Reader = ();
}
