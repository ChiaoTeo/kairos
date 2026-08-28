//! Shared mechanics for borrowed business-event views.

use crate::generated::kairos::common::v_2::EventMetadata;

/// Closed event classification owned by one business contract.
pub trait BusinessEventKind: Copy + Eq + std::fmt::Debug {
    /// Stable snake-case value exposed at process and language boundaries.
    fn as_str(self) -> &'static str;
}

/// A validated event view that borrows its process-boundary frame.
///
/// Implementations are owned by business contracts. This trait only unifies
/// the process mechanics needed by transports and language bindings.
pub trait BorrowedEventView<'a> {
    type Kind: BusinessEventKind;

    fn kind(&self) -> Self::Kind;

    fn metadata(&self) -> EventMetadata<'a>;
}
