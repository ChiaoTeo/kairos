use serde_json::json;

use crate::application::capabilities::reference::{
    AsyncInstrumentCatalogConnection, ExternalInstrumentCatalog,
};
use crate::application::{ConnectionDescriptor, IntegrationError};
use crate::services::participants::hyperliquid::reference as normalization;
use crate::services::transport::http::AsyncPublicHttpClient;

use super::connection::map_exchange_error;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum HyperliquidInstrumentProduct {
    Perpetual,
    Spot,
}

impl HyperliquidInstrumentProduct {
    fn request_type(self) -> &'static str {
        match self {
            Self::Perpetual => "metaAndAssetCtxs",
            Self::Spot => "spotMetaAndAssetCtxs",
        }
    }
}

pub struct HyperliquidInstrumentCatalog {
    pub(super) descriptor: ConnectionDescriptor,
    pub(super) endpoint: String,
    pub(super) client: AsyncPublicHttpClient,
    pub(super) product: HyperliquidInstrumentProduct,
}

impl HyperliquidInstrumentCatalog {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl AsyncInstrumentCatalogConnection for HyperliquidInstrumentCatalog {
    async fn fetch_instruments(&mut self) -> Result<ExternalInstrumentCatalog, IntegrationError> {
        let payload = self
            .client
            .post_query_json_with_headers(
                &self.endpoint,
                &[],
                &json!({"type": self.product.request_type()}),
            )
            .await
            .map_err(map_exchange_error)?;
        match self.product {
            HyperliquidInstrumentProduct::Perpetual => normalization::normalize_perpetual(&payload),
            HyperliquidInstrumentProduct::Spot => normalization::normalize_spot(&payload),
        }
    }
}

pub mod blocking {
    use serde_json::json;

    use crate::application::capabilities::reference::{
        ExternalInstrumentCatalog, InstrumentCatalogConnection,
    };
    use crate::application::{ConnectionDescriptor, IntegrationError};
    use crate::services::participants::hyperliquid::reference as normalization;
    use crate::services::transport::http::PublicHttpClient;

    use super::HyperliquidInstrumentProduct;

    pub struct HyperliquidInstrumentCatalog {
        pub(in crate::application::participants::hyperliquid) descriptor: ConnectionDescriptor,
        pub(in crate::application::participants::hyperliquid) endpoint: String,
        pub(in crate::application::participants::hyperliquid) client: PublicHttpClient,
        pub(in crate::application::participants::hyperliquid) product: HyperliquidInstrumentProduct,
    }

    impl HyperliquidInstrumentCatalog {
        pub fn descriptor(&self) -> &ConnectionDescriptor {
            &self.descriptor
        }
    }

    impl InstrumentCatalogConnection for HyperliquidInstrumentCatalog {
        fn fetch_instruments(&mut self) -> Result<ExternalInstrumentCatalog, IntegrationError> {
            if tokio::runtime::Handle::try_current().is_ok() {
                return Err(IntegrationError::InvalidRequest(
                    "blocking Hyperliquid API cannot run on a Tokio runtime worker".into(),
                ));
            }
            let payload = self
                .client
                .post_query_json_with_headers(
                    &self.endpoint,
                    &[],
                    &json!({"type": self.product.request_type()}),
                )
                .map_err(|error| IntegrationError::Transport(error.to_string()))?;
            match self.product {
                HyperliquidInstrumentProduct::Perpetual => {
                    normalization::normalize_perpetual(&payload)
                }
                HyperliquidInstrumentProduct::Spot => normalization::normalize_spot(&payload),
            }
        }
    }
}
