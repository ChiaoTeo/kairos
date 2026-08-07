#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum IntegrationCapability {
    Reference,
    MarketData,
    AccountRead,
    AccountCredentialInspection,
    AccountMarketProfileRead,
    AccountStream,
    ExecutionStream,
    OrderEntry,
    OrderRead,
    MarketStream,
    Transfer,
    Earn,
}
