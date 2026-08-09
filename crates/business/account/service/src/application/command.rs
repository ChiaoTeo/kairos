#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RefreshAccount {
    pub account_id: String,
    pub segments: Vec<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReconcileAccount {
    pub account_id: String,
    pub segments: Vec<String>,
}
