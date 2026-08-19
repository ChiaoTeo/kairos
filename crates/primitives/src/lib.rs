//! Small, infrastructure-free value objects shared by multiple modules.
//!
//! Wire formats deliberately remain outside this crate. Adapters should use
//! the fallible constructors and explicit accessors at contract boundaries.

mod decimal;
mod error;
mod identity;
mod reference;
mod time;
mod trading;

pub use decimal::*;
pub use error::*;
pub use identity::*;
pub use reference::*;
pub use time::*;
pub use trading::*;

#[cfg(test)]
mod tests {
    use super::{BrokerId, Exchange, InstrumentId, ListingId, MarketId};

    #[test]
    fn spot_identity_keeps_asset_and_market_context_separate() {
        let exchange = Exchange::new("exchange:binance").unwrap();

        assert_eq!(
            InstrumentId::spot("btc").unwrap().as_str(),
            "instrument:spot:BTC"
        );
        assert_eq!(
            ListingId::spot(&exchange, "btc", "usdt").unwrap().as_str(),
            "listing:binance:spot:BTC:USDT"
        );
        assert_eq!(
            MarketId::spot(&exchange, "btcusdt").unwrap().as_str(),
            "market:binance:spot:BTCUSDT"
        );
    }

    #[test]
    fn broker_identity_is_distinct_and_validated() {
        assert_eq!(BrokerId::new("binance").unwrap().as_str(), "binance");
        assert!(BrokerId::new("").is_err());
        assert!(BrokerId::new(" binance").is_err());
    }
}

#[cfg(test)]
mod more_tests {
    use super::*;

    #[test]
    fn text_types_reject_ambiguous_whitespace() {
        assert!(Symbol::new(" BTC").is_err());
        assert!(Exchange::new("").is_err());
        assert_eq!(Currency::new("USD").unwrap().as_str(), "USD");
    }

    #[test]
    fn numeric_types_are_semantically_distinct() {
        let quantity = Quantity::new(10, 2).unwrap();
        let price = Price::new(10, 2).unwrap();
        let price_delta = PriceDelta::new(0, 2).unwrap();
        assert_eq!(quantity.mantissa(), price.mantissa());
        assert_eq!(price_delta.to_string(), "0");
        assert!(Quantity::positive(0, 0).is_err());
    }

    #[test]
    fn decimals_are_canonical_and_compare_by_numeric_value() {
        assert_eq!(Quantity::new(100, 2).unwrap(), Quantity::new(1, 0).unwrap());
        assert!(Quantity::new(15, 1).unwrap() < Quantity::new(2, 0).unwrap());
        assert_eq!(Money::new(-1200, 3).unwrap().to_string(), "-1.2");
        assert_eq!(SignedQuantity::new(0, 18).unwrap().scale(), 0);
    }

    #[test]
    fn decimal_parts_preserve_boundary_scale_and_validate_precision() {
        let value = "-12.50".parse::<DecimalParts>().unwrap();
        assert_eq!(value.mantissa(), -1_250);
        assert_eq!(value.scale(), 2);
        assert_eq!(value.to_string(), "-12.50");
        assert_eq!(serde_json::to_string(&value).unwrap(), "\"-12.50\"");
        assert_eq!(
            serde_json::from_str::<DecimalParts>("\"-12.50\"").unwrap(),
            value
        );
        assert!("0.0000000000000000001".parse::<DecimalParts>().is_err());
        assert!("+1".parse::<DecimalParts>().is_err());
        assert_eq!(
            Money::try_from(value).unwrap(),
            Money::new(-1_250, 2).unwrap()
        );
        assert!(Quantity::try_from(value).is_err());
        assert!("-0".parse::<Quantity>().is_err());
    }

    #[test]
    fn decimal_json_is_a_validated_canonical_string() {
        let price = serde_json::from_str::<Price>("\"42110.500\"").unwrap();
        assert_eq!(price, Price::new(421_105, 1).unwrap());
        assert_eq!(serde_json::to_string(&price).unwrap(), "\"42110.5\"");
        assert!(serde_json::from_str::<Price>(r#"{"mantissa":421105,"scale":1}"#).is_err());
        assert!(serde_json::from_str::<Price>("\"0\"").is_err());
        assert!(serde_json::from_str::<Quantity>("\"0.0000000000000000001\"").is_err());
    }

    #[test]
    fn increments_are_checked_with_exact_decimal_arithmetic() {
        assert!(Quantity::new(125, 3)
            .unwrap()
            .is_multiple_of(Quantity::new(5, 3).unwrap())
            .unwrap());
        assert!(!Price::new(10_001, 2)
            .unwrap()
            .is_multiple_of(Price::new(5, 2).unwrap())
            .unwrap());
    }

    #[test]
    fn reference_status_reads_legacy_provider_break_as_inactive() {
        assert_eq!(ReferenceStatus::from("break"), ReferenceStatus::Inactive);
        assert_eq!(
            serde_json::from_str::<ReferenceStatus>("\"break\"").unwrap(),
            ReferenceStatus::Inactive
        );
        assert_eq!(
            serde_json::to_string(&ReferenceStatus::Inactive).unwrap(),
            "\"inactive\""
        );
        assert_eq!(
            serde_json::from_str::<ReferenceStatus>("\"halted\"").unwrap(),
            ReferenceStatus::Unknown
        );
    }

    #[test]
    fn canonical_reference_taxonomy_rejects_provider_vocabulary() {
        assert_eq!(
            "spot".parse::<InstrumentKind>().unwrap(),
            InstrumentKind::Spot
        );
        assert_eq!(
            "perpetual".parse::<InstrumentKind>().unwrap(),
            InstrumentKind::Perpetual
        );
        assert!("margin".parse::<InstrumentKind>().is_err());
        assert!("usd-m-futures".parse::<InstrumentKind>().is_err());
        assert!("swap".parse::<InstrumentKind>().is_err());
        assert_eq!("crypto".parse::<AssetClass>().unwrap(), AssetClass::Crypto);
        assert!("coin-m".parse::<AssetClass>().is_err());
    }

    #[test]
    fn canonical_reference_taxonomy_has_stable_wire_spellings() {
        for (kind, spelling) in [
            (InstrumentKind::Equity, "equity"),
            (InstrumentKind::Spot, "spot"),
            (InstrumentKind::Perpetual, "perpetual"),
            (InstrumentKind::Future, "future"),
            (InstrumentKind::Option, "option"),
            (InstrumentKind::Index, "index"),
        ] {
            assert_eq!(
                serde_json::to_string(&kind).unwrap(),
                format!("\"{spelling}\"")
            );
            assert_eq!(
                serde_json::from_str::<InstrumentKind>(&format!("\"{spelling}\"")).unwrap(),
                kind
            );
        }
        assert!(serde_json::from_str::<InstrumentKind>("\"future-value\"").is_err());
    }
}
