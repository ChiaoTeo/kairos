//! Normalized credential inspection used to discover account capabilities.

use std::collections::BTreeMap;

use crate::application::Connection;

#[derive(Clone, Debug, Default, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ExternalAccountCredentialProfile {
    pub remote_identity: Option<String>,
    pub account_type: Option<String>,
    pub permissions: Vec<String>,
    pub segments: Vec<String>,
    pub attributes: BTreeMap<String, String>,
}

pub trait AccountCredentialInspectionConnection: Connection {
    fn inspect_credential(&mut self) -> Result<ExternalAccountCredentialProfile, String>;
}
