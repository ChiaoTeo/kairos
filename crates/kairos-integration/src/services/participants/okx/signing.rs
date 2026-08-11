//! OKX request signing and pre-hash construction.

use hmac::{Hmac, Mac};
use sha2::Sha256;

use crate::services::transport::http::ExchangeError;

type HmacSha256 = Hmac<Sha256>;

/// OKX signs `timestamp + method + request_path + query_or_body` and encodes
/// the HMAC digest as base64.
pub(crate) fn okx_signature(
    secret_key: &str,
    timestamp: &str,
    method: &str,
    request_path: &str,
    query_or_body: &str,
) -> Result<String, ExchangeError> {
    let mut mac = HmacSha256::new_from_slice(secret_key.trim().as_bytes())
        .map_err(|error| ExchangeError::Authentication(error.to_string()))?;
    mac.update(format!("{timestamp}{method}{request_path}{query_or_body}").as_bytes());
    use base64::Engine;
    Ok(base64::engine::general_purpose::STANDARD.encode(mac.finalize().into_bytes()))
}
