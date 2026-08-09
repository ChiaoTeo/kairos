#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CommandEnvelope {
    pub command_type: String,
    pub request_id: String,
    pub payload: Vec<u8>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct QueryEnvelope {
    pub query_type: String,
    pub request_id: String,
    pub payload: Vec<u8>,
}
