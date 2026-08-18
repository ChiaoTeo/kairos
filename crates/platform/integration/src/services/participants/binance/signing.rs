//! Binance request signing and canonical query construction.

use hmac::{Hmac, Mac};
use sha2::Sha256;

use crate::transport::http::ExchangeError;

type HmacSha256 = Hmac<Sha256>;

pub(crate) fn sign_query(secret_key: &str, query: &str) -> Result<String, ExchangeError> {
    let mut mac = HmacSha256::new_from_slice(secret_key.trim().as_bytes())
        .map_err(|error| ExchangeError::Authentication(error.to_string()))?;
    mac.update(query.as_bytes());
    Ok(hex::encode(mac.finalize().into_bytes()))
}

#[cfg(test)]
mod tests {
    use super::sign_query;

    #[test]
    fn signs_query_with_hmac_sha256() {
        assert_eq!(
            sign_query("key", "The quick brown fox jumps over the lazy dog").unwrap(),
            "f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8"
        );
    }
}
