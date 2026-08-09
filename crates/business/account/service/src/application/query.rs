#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AccountQuery {
    pub account_id: String,
    pub segments: Vec<String>,
    pub max_age_seconds: Option<u64>,
    pub now_unix_nanos: Option<u64>,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct AccountDataQuery {
    pub account_id: Option<String>,
    pub segments: Vec<String>,
    pub symbol: Option<String>,
    pub include_zero: bool,
    pub limit: Option<usize>,
    pub page: Option<usize>,
    pub page_size: Option<usize>,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct AccountMarketProfileRequest {
    pub account_id: String,
    pub segment_key: String,
    pub market_id: String,
    pub source_symbol: String,
}
