mod key;
mod metadata;
pub mod encode;
pub use key::{RiskViewKey,RiskViewKind};
pub use metadata::ViewMetadata;
use std::path::Path;
use crate::{ContractError,ContractResult};
pub struct ViewFrame { generation:u64, bytes:Vec<u8> }
impl ViewFrame { pub(crate) fn new(generation:u64,bytes:Vec<u8>)->Self{Self{generation,bytes}} pub fn generation(&self)->u64{self.generation} pub fn bytes(&self)->&[u8]{&self.bytes} pub fn decode(&self)->ContractResult<kairos_protocol::generated::kairos::risk::v_2::RiskLatestView<'_>>{if !kairos_protocol::generated::kairos::risk::v_2::risk_latest_view_buffer_has_identifier(&self.bytes){return Err(ContractError::Invalid("expected RXV2 RiskLatestView".into()));}kairos_protocol::generated::kairos::risk::v_2::root_as_risk_latest_view(&self.bytes).map_err(|e|ContractError::Invalid(e.to_string()))} }
pub struct RiskViewReader { key:RiskViewKey, reader:crate::transport::RiskMmapReader }
impl RiskViewReader { pub fn open(root:impl AsRef<Path>,key:RiskViewKey)->ContractResult<Self>{Ok(Self{reader:crate::transport::RiskMmapReader::open(root,key.clone())?,key})} pub fn read(&self)->ContractResult<ViewFrame>{self.reader.read()} pub fn key(&self)->&RiskViewKey{&self.key} }
