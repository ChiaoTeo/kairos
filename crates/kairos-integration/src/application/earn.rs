use crate::application::connection::Connection;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EarnProductType {
    Flexible,
    Locked,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EarnProduct {
    pub product_id: String,
    pub asset: String,
    pub product_type: EarnProductType,
    pub annual_rate: String,
    pub min_amount: String,
    pub max_amount: String,
    pub status: String,
    pub duration_days: Option<u32>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EarnPosition {
    pub product_id: String,
    pub asset: String,
    pub amount: String,
    pub rewards: String,
    pub annual_rate: String,
    pub status: String,
    pub updated_at_unix_millis: Option<u64>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EarnReward {
    pub asset: String,
    pub amount: String,
    pub product_id: Option<String>,
    pub occurred_at_unix_millis: Option<u64>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EarnSubscribeRequest {
    pub product_id: String,
    pub product_type: EarnProductType,
    pub amount: String,
    pub auto_renew: Option<bool>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EarnRedeemRequest {
    pub product_id: String,
    pub product_type: EarnProductType,
    pub amount: Option<String>,
    pub destination_account: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EarnActionResult {
    pub accepted: bool,
    pub action_id: Option<String>,
    pub status: String,
    pub reason: String,
}

pub trait EarnConnection: Connection {
    fn products(
        &mut self,
        asset: Option<&str>,
        product_type: Option<EarnProductType>,
    ) -> Result<Vec<EarnProduct>, String>;
    fn positions(&mut self, asset: Option<&str>) -> Result<Vec<EarnPosition>, String>;
    fn rewards(&mut self, asset: Option<&str>) -> Result<Vec<EarnReward>, String>;
    fn subscribe(&mut self, request: &EarnSubscribeRequest) -> Result<EarnActionResult, String>;
    fn redeem(&mut self, request: &EarnRedeemRequest) -> Result<EarnActionResult, String>;
}
