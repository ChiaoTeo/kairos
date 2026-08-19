use serde::{Deserialize, Serialize};

use crate::domain::observation::ObservationKind;

#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub struct ObservationSelector {
    pub kind: Option<ObservationKind>,
    pub qualifier: Option<String>,
}

impl ObservationSelector {
    pub fn parse(value: &str) -> Result<Self, String> {
        let value = value.trim();
        if value.is_empty() {
            return Err("subscription selector cannot be blank".into());
        }
        let (kind, qualifier) = value.split_once(':').unwrap_or((value, ""));
        if kind == "*" {
            if !qualifier.is_empty() {
                return Err(format!(
                    "wildcard selector cannot have a qualifier: {value}"
                ));
            }
            return Ok(Self {
                kind: None,
                qualifier: None,
            });
        }
        let kind = ObservationKind::parse_selector(kind)
            .map_err(|_| format!("unsupported market selector: {value}"))?;
        let supports_qualifier = matches!(
            kind,
            ObservationKind::Bar
                | ObservationKind::TradeBar
                | ObservationKind::QuoteBar
                | ObservationKind::Rate
                | ObservationKind::FundingRate
        );
        if !supports_qualifier && !qualifier.is_empty() {
            return Err(format!(
                "selector qualifier is only supported for bars and rates: {value}"
            ));
        }
        if supports_qualifier && value.contains(':') && qualifier.trim().is_empty() {
            return Err(format!("selector qualifier cannot be blank: {value}"));
        }
        Ok(Self {
            kind: Some(kind),
            qualifier: (!qualifier.is_empty()).then(|| qualifier.to_owned()),
        })
    }

    pub fn matches(&self, kind: ObservationKind, qualifier: Option<&str>) -> bool {
        let kind_matches = self.kind.is_none()
            || self.kind == Some(kind)
            || matches!(
                (kind, self.kind),
                (ObservationKind::Rate, Some(ObservationKind::FundingRate))
                    | (ObservationKind::FundingRate, Some(ObservationKind::Rate))
            );
        kind_matches
            && self
                .qualifier
                .as_deref()
                .is_none_or(|selected| qualifier == Some(selected))
    }
}

pub fn selector_matches_observation(
    selectors: &[ObservationSelector],
    kind: ObservationKind,
    qualifier: Option<&str>,
) -> bool {
    selectors.is_empty()
        || selectors
            .iter()
            .any(|selector| selector.matches(kind, qualifier))
}

pub fn selector_matches_orderbook(selectors: &[ObservationSelector]) -> bool {
    selector_matches_observation(selectors, ObservationKind::OrderBook, None)
}

#[cfg(test)]
mod tests {
    use super::{ObservationSelector, selector_matches_observation};
    use crate::domain::observation::ObservationKind;

    #[test]
    fn derivative_selectors_support_qualified_rates() {
        let selectors = ["funding_rate:8h", "mark_price"]
            .into_iter()
            .map(ObservationSelector::parse)
            .collect::<Result<Vec<_>, _>>()
            .unwrap();
        assert!(selector_matches_observation(
            &selectors,
            ObservationKind::Rate,
            Some("8h")
        ));
        assert!(selector_matches_observation(
            &selectors,
            ObservationKind::MarkPrice,
            None
        ));
    }
}
