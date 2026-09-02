use std::collections::{BTreeMap, BTreeSet};

use sqlx::{Row, SqlitePool};

use crate::domain::Listing;

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub(crate) struct StartupAuditReport {
    pub missing_current_equity_markets: Vec<MissingEquityMarket>,
    pub missing_provider_equity_markets: Vec<MissingProviderEquityMarket>,
    pub reset_providers: Vec<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct MissingEquityMarket {
    pub listing_id: String,
    pub expected_market_id: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct MissingProviderEquityMarket {
    pub provider: String,
    pub listing_id: String,
    pub expected_market_id: String,
}

pub(crate) async fn startup_audit(pool: &SqlitePool) -> sqlx::Result<StartupAuditReport> {
    let missing_current_equity_markets = missing_current_equity_markets(pool).await?;
    let missing_provider_equity_markets = missing_provider_equity_markets(pool).await?;
    Ok(StartupAuditReport {
        missing_current_equity_markets,
        missing_provider_equity_markets,
        reset_providers: Vec::new(),
    })
}

async fn missing_current_equity_markets(
    pool: &SqlitePool,
) -> sqlx::Result<Vec<MissingEquityMarket>> {
    let rows = sqlx::query(
        "SELECT payload FROM reference_listings_current \
         WHERE status IN ('active', 'trading') \
         ORDER BY listing_id",
    )
    .fetch_all(pool)
    .await?;
    let existing_markets = current_market_ids(pool).await?;
    let mut missing = Vec::new();
    for row in rows {
        let payload = row.try_get::<String, _>("payload")?;
        let listing = decode_listing(payload)?;
        let Some(expected_market_id) = expected_equity_market_id(&listing)? else {
            continue;
        };
        if !existing_markets.contains(&expected_market_id) {
            missing.push(MissingEquityMarket {
                listing_id: listing.listing_id.to_string(),
                expected_market_id,
            });
        }
    }
    Ok(missing)
}

async fn missing_provider_equity_markets(
    pool: &SqlitePool,
) -> sqlx::Result<Vec<MissingProviderEquityMarket>> {
    let rows = sqlx::query(
        "SELECT provider, record_kind, record_id, payload \
         FROM reference_provider_records \
         WHERE record_kind IN ('listing', 'market') \
         ORDER BY provider, record_kind, record_id",
    )
    .fetch_all(pool)
    .await?;
    let mut listings = BTreeMap::<String, Vec<Listing>>::new();
    let mut market_ids = BTreeMap::<String, BTreeSet<String>>::new();
    for row in rows {
        let provider = row.try_get::<String, _>("provider")?;
        let kind = row.try_get::<String, _>("record_kind")?;
        match kind.as_str() {
            "listing" => {
                let listing = decode_listing(row.try_get("payload")?)?;
                listings.entry(provider).or_default().push(listing);
            },
            "market" => {
                market_ids
                    .entry(provider)
                    .or_default()
                    .insert(row.try_get("record_id")?);
            },
            _ => {},
        }
    }

    let mut missing = Vec::new();
    for (provider, listings) in listings {
        let provider_market_ids = market_ids.get(&provider);
        for listing in listings {
            if !is_active_reference_status(&listing.status) {
                continue;
            }
            let Some(expected_market_id) = expected_equity_market_id(&listing)? else {
                continue;
            };
            if provider_market_ids.is_some_and(|ids| ids.contains(&expected_market_id)) {
                continue;
            }
            missing.push(MissingProviderEquityMarket {
                provider: provider.clone(),
                listing_id: listing.listing_id.to_string(),
                expected_market_id,
            });
        }
    }
    Ok(missing)
}

async fn current_market_ids(pool: &SqlitePool) -> sqlx::Result<BTreeSet<String>> {
    sqlx::query_scalar::<_, String>("SELECT market_id FROM reference_markets_current")
        .fetch_all(pool)
        .await
        .map(|values| values.into_iter().collect())
}

fn expected_equity_market_id(listing: &Listing) -> sqlx::Result<Option<String>> {
    let listing_id = listing.listing_id.to_string();
    if !listing_id.contains(":equity:") {
        return Ok(None);
    }
    let symbol = listing.exchange_symbol.to_string();
    let market_id = kairos_primitives::reference::MarketId::venue(
        &listing.exchange_id,
        kairos_primitives::reference::InstrumentKind::Equity,
        format!("{symbol}:USD"),
    )
    .map_err(|error| sqlx::Error::Protocol(error.to_string()))?;
    Ok(Some(market_id.to_string()))
}

fn is_active_reference_status(status: &kairos_primitives::reference::ReferenceStatus) -> bool {
    matches!(status.to_string().as_str(), "active" | "trading")
}

fn decode_listing(payload: String) -> sqlx::Result<Listing> {
    serde_json::from_str(&payload).map_err(|error| sqlx::Error::Protocol(error.to_string()))
}
