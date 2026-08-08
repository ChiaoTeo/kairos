//! Reference-facing integration capability.

use crate::application::Connection;

pub use crate::domain::reference::{
    ReferenceAsset, ReferenceCatalogPayload, ReferenceEntity, ReferenceFinancialProduct,
    ReferenceInstrument, ReferenceListing, ReferenceMarket,
};

pub trait ReferenceDataConnection: Connection {
    fn fetch_reference_catalog(&mut self) -> Result<ReferenceCatalogPayload, String>;
}
