use serde_json::json;

use crate::application::capabilities::reference::{
    AsyncInstrumentCatalogConnection, ExternalInstrumentCatalog,
};
use crate::application::{ConnectionDescriptor, IntegrationError};
use crate::services::participants::hyperliquid::reference as normalization;
use crate::services::transport::http::AsyncPublicHttpClient;

use super::connection::map_exchange_error;

pub struct HyperliquidInstrumentCatalog {
    pub(super) descriptor: ConnectionDescriptor,
    pub(super) endpoint: String,
    pub(super) client: AsyncPublicHttpClient,
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
            .post_query_json_with_headers(&self.endpoint, &[], &json!({"type": "metaAndAssetCtxs"}))
            .await
            .map_err(map_exchange_error)?;
        normalization::normalize(&payload)
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

    pub struct HyperliquidInstrumentCatalog {
        pub(in crate::application::participants::hyperliquid) descriptor: ConnectionDescriptor,
        pub(in crate::application::participants::hyperliquid) endpoint: String,
        pub(in crate::application::participants::hyperliquid) client: PublicHttpClient,
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
                    &json!({"type": "metaAndAssetCtxs"}),
                )
                .map_err(|error| IntegrationError::Transport(error.to_string()))?;
            normalization::normalize(&payload)
        }
    }
}
