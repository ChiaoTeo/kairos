//! Stable description of one integration connection.

use super::{AccessScope, AssetType, IntegrationCapability, ProductFamily, TransportKind};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ConnectionSpec {
    pub connection_id: String,
    pub provider: String,
    pub product: Option<ProductFamily>,
    pub access: AccessScope,
    pub transport: TransportKind,
    pub capability: IntegrationCapability,
    pub credential_id: Option<String>,
    pub asset_type: Option<AssetType>,
}

impl ConnectionSpec {
    pub fn validate(&self) -> Result<(), String> {
        if self.connection_id.trim().is_empty() {
            return Err("connection id is required".into());
        }
        if self.provider.trim().is_empty() {
            return Err("connection provider is required".into());
        }
        if self.access == AccessScope::Private && self.credential_id.is_none() {
            return Err("private connection requires a credential id".into());
        }
        Ok(())
    }
}
