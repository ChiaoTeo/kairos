use std::str::FromStr;

use kairos_primitives::{
    Money, Price, PriceDelta, Quantity, Rate, SignedQuantity, MAX_DECIMAL_SCALE,
};
use proptest::prelude::*;

proptest! {
    #[test]
    fn signed_decimals_round_trip_through_json(
        mantissa in any::<i64>(),
        scale in 0_u8..=MAX_DECIMAL_SCALE,
    ) {
        let values = [
            serde_json::to_string(&Money::new(mantissa, scale).unwrap()).unwrap(),
            serde_json::to_string(&Rate::new(mantissa, scale).unwrap()).unwrap(),
            serde_json::to_string(&PriceDelta::new(mantissa, scale).unwrap()).unwrap(),
            serde_json::to_string(&SignedQuantity::new(mantissa, scale).unwrap()).unwrap(),
        ];

        prop_assert_eq!(serde_json::from_str::<Money>(&values[0]).unwrap(), Money::new(mantissa, scale).unwrap());
        prop_assert_eq!(serde_json::from_str::<Rate>(&values[1]).unwrap(), Rate::new(mantissa, scale).unwrap());
        prop_assert_eq!(serde_json::from_str::<PriceDelta>(&values[2]).unwrap(), PriceDelta::new(mantissa, scale).unwrap());
        prop_assert_eq!(serde_json::from_str::<SignedQuantity>(&values[3]).unwrap(), SignedQuantity::new(mantissa, scale).unwrap());
    }

    #[test]
    fn unsigned_decimals_round_trip_through_json(
        mantissa in 0_i64..=i64::MAX,
        scale in 0_u8..=MAX_DECIMAL_SCALE,
    ) {
        let quantity = Quantity::new(mantissa, scale).unwrap();
        let encoded = serde_json::to_string(&quantity).unwrap();
        prop_assert_eq!(serde_json::from_str::<Quantity>(&encoded).unwrap(), quantity);

        if mantissa > 0 {
            let price = Price::new(mantissa, scale).unwrap();
            let encoded = serde_json::to_string(&price).unwrap();
            prop_assert_eq!(serde_json::from_str::<Price>(&encoded).unwrap(), price);
        }
    }

    #[test]
    fn normalized_scale_does_not_change_decimal_value(
        mantissa in -922_337_203_685_477_580_i64..=922_337_203_685_477_580_i64,
        scale in 0_u8..MAX_DECIMAL_SCALE,
    ) {
        let original = Money::new(mantissa, scale).unwrap();
        let with_trailing_zero = Money::new(mantissa * 10, scale + 1).unwrap();
        prop_assert_eq!(original, with_trailing_zero);
        prop_assert_eq!(original.cmp(&with_trailing_zero), std::cmp::Ordering::Equal);
    }
}

#[test]
fn signed_minimum_formats_and_parses_without_overflow() {
    let value = Money::new(i64::MIN, 18).unwrap();
    let text = value.to_string();
    assert_eq!(Money::from_str(&text).unwrap(), value);
}

#[test]
fn positive_values_reject_zero_and_negative_inputs() {
    assert!(Price::new(0, 0).is_err());
    assert!(Price::new(-1, 0).is_err());
    assert!(Quantity::new(-1, 0).is_err());
    assert!(Quantity::positive(0, 0).is_err());
}
