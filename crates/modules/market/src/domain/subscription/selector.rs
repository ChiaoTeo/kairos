use serde::{Deserialize, Serialize};

use crate::domain::observation::ObservationKind;

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ObservationSelectorError {
    Blank,
    WildcardQualifier { selector: String },
    Unsupported { selector: String },
    QualifierUnsupported { selector: String },
    BlankQualifier { selector: String },
}

impl ObservationSelectorError {
    pub const fn code(&self) -> &'static str {
        match self {
            Self::Blank => "market.subscription.selector_blank",
            Self::WildcardQualifier { .. } => "market.subscription.wildcard_qualifier_forbidden",
            Self::Unsupported { .. } => "market.subscription.selector_unsupported",
            Self::QualifierUnsupported { .. } => "market.subscription.qualifier_unsupported",
            Self::BlankQualifier { .. } => "market.subscription.qualifier_blank",
        }
    }
}

impl std::fmt::Display for ObservationSelectorError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Blank => formatter.write_str("subscription selector cannot be blank"),
            Self::WildcardQualifier { selector } => {
                write!(
                    formatter,
                    "wildcard selector cannot have a qualifier: {selector}"
                )
            },
            Self::Unsupported { selector } => {
                write!(formatter, "unsupported market selector: {selector}")
            },
            Self::QualifierUnsupported { selector } => write!(
                formatter,
                "selector qualifier is only supported for bars and rates: {selector}"
            ),
            Self::BlankQualifier { selector } => {
                write!(formatter, "selector qualifier cannot be blank: {selector}")
            },
        }
    }
}

impl std::error::Error for ObservationSelectorError {}

#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
pub struct ObservationSelector {
    pub kind: Option<ObservationKind>,
    pub qualifier: Option<String>,
}

impl ObservationSelector {
    pub fn parse(value: &str) -> Result<Self, ObservationSelectorError> {
        let value = value.trim();
        if value.is_empty() {
            return Err(ObservationSelectorError::Blank);
        }
        let (kind, qualifier) = value.split_once(':').unwrap_or((value, ""));
        if kind == "*" {
            if !qualifier.is_empty() {
                return Err(ObservationSelectorError::WildcardQualifier {
                    selector: value.to_owned(),
                });
            }
            return Ok(Self {
                kind: None,
                qualifier: None,
            });
        }
        let kind = ObservationKind::parse_selector(kind).map_err(|_| {
            ObservationSelectorError::Unsupported {
                selector: value.to_owned(),
            }
        })?;
        let supports_qualifier = matches!(
            kind,
            ObservationKind::Bar
                | ObservationKind::TradeBar
                | ObservationKind::QuoteBar
                | ObservationKind::Rate
                | ObservationKind::FundingRate
        );
        if !supports_qualifier && !qualifier.is_empty() {
            return Err(ObservationSelectorError::QualifierUnsupported {
                selector: value.to_owned(),
            });
        }
        if supports_qualifier && value.contains(':') && qualifier.trim().is_empty() {
            return Err(ObservationSelectorError::BlankQualifier {
                selector: value.to_owned(),
            });
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
    use super::{ObservationSelector, ObservationSelectorError, selector_matches_observation};
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

    #[test]
    fn invalid_selector_preserves_category_and_input() {
        let error = ObservationSelector::parse("quote:1m").unwrap_err();
        assert_eq!(
            error,
            ObservationSelectorError::QualifierUnsupported {
                selector: "quote:1m".into()
            }
        );
        assert_eq!(error.code(), "market.subscription.qualifier_unsupported");
    }
}
