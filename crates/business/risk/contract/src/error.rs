#[derive(Debug)]
pub enum ContractError { Invalid(String), Transport(String), Rejected(String), Unsupported(String) }
impl std::fmt::Display for ContractError { fn fmt(&self, f:&mut std::fmt::Formatter<'_>)->std::fmt::Result { match self { Self::Invalid(v)=>write!(f,"invalid Risk contract data: {v}"), Self::Transport(v)=>write!(f,"Risk contract transport failed: {v}"), Self::Rejected(v)=>write!(f,"Risk command rejected: {v}"), Self::Unsupported(v)=>write!(f,"unsupported Risk contract operation: {v}") } } }
impl std::error::Error for ContractError {}
pub type ContractResult<T> = Result<T, ContractError>;
