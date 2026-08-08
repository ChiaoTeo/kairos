//! Stable description of one integration connection.

use super::{
    AccessScope, AssetType, IntegrationCapability, IntegrationRoute, ProductFamily, TransportKind,
};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ConnectionSpec {
    pub connection_id: String,
    pub route: IntegrationRoute,
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
        self.route.validate()?;
        if self.access == AccessScope::Private && self.credential_id.is_none() {
            return Err("private connection requires a credential id".into());
        }
        if self.route.data_provider.is_some() && self.access == AccessScope::Private {
            return Err("data provider connections cannot use private access".into());
        }
        if self.route.data_provider.is_some()
            && !matches!(
                self.capability,
                IntegrationCapability::Reference
                    | IntegrationCapability::MarketData
                    | IntegrationCapability::MarketStream
            )
        {
            return Err(
                "data provider connections only support market/reference capabilities".into(),
            );
        }
        if matches!(
            self.capability,
            IntegrationCapability::AccountRead
                | IntegrationCapability::AccountCredentialInspection
                | IntegrationCapability::AccountMarketProfileRead
                | IntegrationCapability::AccountStream
                | IntegrationCapability::ExecutionStream
                | IntegrationCapability::OrderEntry
                | IntegrationCapability::OrderRead
                | IntegrationCapability::Transfer
                | IntegrationCapability::Earn
        ) && self.route.broker.is_none()
            && self.route.exchange.is_none()
        {
            return Err("account and execution capabilities require an exchange or broker".into());
        }
        Ok(())
    }
}
