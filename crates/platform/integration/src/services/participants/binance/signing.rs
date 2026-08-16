//! Binance request signing and canonical query construction.

use std::collections::BTreeMap;

use hmac::{Hmac, Mac};
use sha2::Sha256;

use crate::services::transport::http::ExchangeError;

type HmacSha256 = Hmac<Sha256>;

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct SignedQuery {
    pub(crate) query: String,
    pub(crate) signature: String,
}

pub(crate) fn sign_query(secret_key: &str, query: &str) -> Result<String, ExchangeError> {
    let mut mac = HmacSha256::new_from_slice(secret_key.trim().as_bytes())
        .map_err(|error| ExchangeError::Authentication(error.to_string()))?;
    mac.update(query.as_bytes());
    Ok(hex::encode(mac.finalize().into_bytes()))
}

pub(crate) fn signed_query(
    secret_key: &str,
    params: impl IntoIterator<Item = (String, String)>,
) -> Result<SignedQuery, ExchangeError> {
    let query = url::form_urlencoded::Serializer::new(String::new())
        .extend_pairs(BTreeMap::from_iter(params))
        .finish();
    let signature = sign_query(secret_key, &query)?;
    Ok(SignedQuery { query, signature })
}

#[cfg(test)]
mod tests {
    use super::{sign_query, signed_query};

    #[test]
    fn signs_query_with_hmac_sha256() {
        assert_eq!(
            sign_query("key", "The quick brown fox jumps over the lazy dog").unwrap(),
            "f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8"
        );
    }

    #[test]
    fn signed_query_is_stable() {
        let signed = signed_query(
            "secret",
            [
                ("symbol".to_string(), "BTCUSDT".to_string()),
                ("side".to_string(), "BUY".to_string()),
            ],
        )
        .unwrap();
        assert_eq!(signed.query, "side=BUY&symbol=BTCUSDT");
        assert!(!signed.signature.is_empty());
    }
}
