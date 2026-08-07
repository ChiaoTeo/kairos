#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ProductFamily {
    Spot,
    CrossMargin,
    IsolatedMargin,
    UsdMFutures,
    CoinMFutures,
    Options,
    Equity,
    Funding,
    Earn,
}
