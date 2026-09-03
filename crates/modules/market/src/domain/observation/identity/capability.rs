use kairos_primitives::reference::InstrumentKind;

use super::{ObservationIdentityError, ObservationKind};
use crate::domain::subscription::ObservationSelector;

pub(crate) fn validate_observation_selectors(
    instrument_kind: InstrumentKind,
    selectors: &[ObservationSelector],
) -> Result<(), ObservationIdentityError> {
    if instrument_kind == InstrumentKind::Unknown || selectors.is_empty() {
        return Ok(());
    }
    for selector in selectors {
        let Some(kind) = selector.kind else {
            continue;
        };
        if !supports_observation(instrument_kind, kind) {
            return Err(ObservationIdentityError::UnsupportedObservation {
                instrument_kind,
                observation_kind: kind,
            });
        }
    }
    Ok(())
}

fn supports_observation(instrument_kind: InstrumentKind, kind: ObservationKind) -> bool {
    let cash_market = matches!(
        kind,
        ObservationKind::Quote
            | ObservationKind::Trade
            | ObservationKind::Bar
            | ObservationKind::TradeBar
            | ObservationKind::QuoteBar
            | ObservationKind::Ticker24h
            | ObservationKind::OrderBook
    );
    cash_market
        || match instrument_kind {
            InstrumentKind::Perpetual => matches!(
                kind,
                ObservationKind::Rate
                    | ObservationKind::FundingRate
                    | ObservationKind::MarkPrice
                    | ObservationKind::IndexPrice
                    | ObservationKind::OpenInterest
            ),
            InstrumentKind::Future => matches!(
                kind,
                ObservationKind::MarkPrice
                    | ObservationKind::IndexPrice
                    | ObservationKind::OpenInterest
            ),
            InstrumentKind::Option => matches!(
                kind,
                ObservationKind::OptionGreeks
                    | ObservationKind::MarkPrice
                    | ObservationKind::IndexPrice
                    | ObservationKind::OpenInterest
                    | ObservationKind::Rate
            ),
            InstrumentKind::Spot
            | InstrumentKind::Equity
            | InstrumentKind::Index
            | InstrumentKind::Unknown => false,
        }
}

#[cfg(test)]
mod tests {
    use kairos_primitives::reference::InstrumentKind;

    use super::validate_observation_selectors;
    use crate::domain::subscription::ObservationSelector;
    use crate::{ObservationIdentityError, ObservationKind};

    fn selectors(values: &[&str]) -> Vec<ObservationSelector> {
        values
            .iter()
            .map(|value| ObservationSelector::parse(value).unwrap())
            .collect()
    }

    #[test]
    fn canonical_market_kinds_expose_different_observation_capabilities() {
        assert!(
            validate_observation_selectors(
                InstrumentKind::Spot,
                &selectors(&["quote", "orderbook"])
            )
            .is_ok()
        );
        assert_eq!(
            validate_observation_selectors(InstrumentKind::Spot, &selectors(&["funding_rate"])),
            Err(ObservationIdentityError::UnsupportedObservation {
                instrument_kind: InstrumentKind::Spot,
                observation_kind: ObservationKind::FundingRate,
            })
        );
        assert!(
            validate_observation_selectors(
                InstrumentKind::Perpetual,
                &selectors(&["funding_rate", "mark_price"])
            )
            .is_ok()
        );
        assert!(
            validate_observation_selectors(InstrumentKind::Future, &selectors(&["funding_rate"]))
                .is_err()
        );
        assert!(
            validate_observation_selectors(
                InstrumentKind::Option,
                &selectors(&["greeks", "open_interest"])
            )
            .is_ok()
        );
    }
}
