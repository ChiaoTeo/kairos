use crate::application::IntegrationError;
use crate::services::participants::massive::MassiveAsyncRestClient;

use super::connection::map_exchange_error;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MassiveCashDividend {
    pub id: String,
    pub ticker: String,
    pub ex_dividend_date: String,
    pub declaration_date: Option<String>,
    pub record_date: Option<String>,
    pub pay_date: Option<String>,
    pub cash_amount: Option<String>,
    pub split_adjusted_cash_amount: Option<String>,
    pub historical_adjustment_factor: Option<String>,
    pub currency: Option<String>,
    pub distribution_type: Option<String>,
    pub frequency: Option<u32>,
}

pub struct MassiveDividendCatalog {
    pub(super) client: MassiveAsyncRestClient,
}

impl MassiveDividendCatalog {
    pub async fn fetch(
        &mut self,
        ticker: &str,
        start_date: &str,
        end_date: &str,
    ) -> Result<Vec<MassiveCashDividend>, IntegrationError> {
        self.client
            .cash_dividends(ticker, start_date, end_date)
            .await
            .map_err(map_exchange_error)
            .map(|rows| {
                rows.into_iter()
                    .map(|row| MassiveCashDividend {
                        id: row.id,
                        ticker: row.ticker,
                        ex_dividend_date: row.ex_dividend_date,
                        declaration_date: row.declaration_date,
                        record_date: row.record_date,
                        pay_date: row.pay_date,
                        cash_amount: row.cash_amount,
                        split_adjusted_cash_amount: row.split_adjusted_cash_amount,
                        historical_adjustment_factor: row.historical_adjustment_factor,
                        currency: row.currency,
                        distribution_type: row.distribution_type,
                        frequency: row.frequency,
                    })
                    .collect()
            })
    }
}
