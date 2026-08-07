#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AccessScope {
    Public,
    Private,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TransportKind {
    Rest,
    WebSocket,
    RequestApi,
    MarketStream,
    UserStream,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AssetType {
    Crypto,
    Equity,
    Other,
}
