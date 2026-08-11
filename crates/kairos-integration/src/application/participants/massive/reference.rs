use crate::application::capabilities::reference::{
    AsyncInstrumentCatalogConnection, ExternalInstrumentCatalog, ExternalInstrumentCatalogPage,
};
use crate::application::{ConnectionDescriptor, IntegrationError};
use crate::services::participants::massive::reference as normalization;
use crate::services::participants::massive::MassiveAsyncRestClient;

use super::connection::map_exchange_error;

pub struct MassiveInstrumentCatalog {
    pub(super) descriptor: ConnectionDescriptor,
    pub(super) client: MassiveAsyncRestClient,
}

impl MassiveInstrumentCatalog {
    pub fn descriptor(&self) -> &ConnectionDescriptor {
        &self.descriptor
    }
}

impl AsyncInstrumentCatalogConnection for MassiveInstrumentCatalog {
    async fn fetch_instruments(&mut self) -> Result<ExternalInstrumentCatalog, IntegrationError> {
        let rows = self
            .client
            .load_markets()
            .await
            .map_err(map_exchange_error)?;
        normalization::normalize(rows)
    }

    async fn fetch_instruments_page(
        &mut self,
        cursor: Option<&str>,
        limit: usize,
    ) -> Result<ExternalInstrumentCatalogPage, IntegrationError> {
        let page = self
            .client
            .load_markets_page(cursor, limit)
            .await
            .map_err(map_exchange_error)?;
        Ok(ExternalInstrumentCatalogPage {
            catalog: normalization::normalize(page.rows)?,
            next_cursor: page.next_cursor,
            complete: page.complete,
        })
    }
}

pub mod blocking {
    use crate::application::capabilities::reference::{
        ExternalInstrumentCatalog, ExternalInstrumentCatalogPage, InstrumentCatalogConnection,
    };
    use crate::application::{ConnectionDescriptor, IntegrationError};
    use crate::services::participants::massive::reference as normalization;
    use crate::services::participants::massive::reference::MassiveMarketClient;
    use crate::services::participants::massive::MassiveStocksRestClient;

    pub struct MassiveInstrumentCatalog {
        pub(in crate::application::participants::massive) descriptor: ConnectionDescriptor,
        pub(in crate::application::participants::massive) client: MassiveStocksRestClient,
    }

    impl MassiveInstrumentCatalog {
        pub fn descriptor(&self) -> &ConnectionDescriptor {
            &self.descriptor
        }
    }

    impl InstrumentCatalogConnection for MassiveInstrumentCatalog {
        fn fetch_instruments(&mut self) -> Result<ExternalInstrumentCatalog, IntegrationError> {
            super::super::market_data::reject_blocking_runtime()?;
            normalization::normalize(
                self.client
                    .load_markets()
                    .map_err(IntegrationError::Transport)?,
            )
        }

        fn fetch_instruments_page(
            &mut self,
            cursor: Option<&str>,
            limit: usize,
        ) -> Result<ExternalInstrumentCatalogPage, IntegrationError> {
            super::super::market_data::reject_blocking_runtime()?;
            let page = self
                .client
                .load_markets_page(cursor, limit)
                .map_err(IntegrationError::Transport)?;
            Ok(ExternalInstrumentCatalogPage {
                catalog: normalization::normalize(page.rows)?,
                next_cursor: page.next_cursor,
                complete: page.complete,
            })
        }
    }
}
