/// Execution-owned route classification. It is mapped to each participant's
/// native instrument/domain vocabulary only in composition.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RouteProduct {
    Spot,
    CrossMargin,
    IsolatedMargin,
    UsdMFutures,
    CoinMFutures,
    Options,
    Equity,
}
