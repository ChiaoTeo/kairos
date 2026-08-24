use crate::text::text_type;

text_type!(CapitalGroupId);
text_type!(FundingObjectiveId);
text_type!(CapitalDemandId);
text_type!(CapitalRouteId);
text_type!(CapitalPlanId);
text_type!(CapitalReservationId);
text_type!(CapitalOperationId);
// Capital authorization identity (for example a lease authority), not a
// market-data provider or broker identity.
text_type!(CapitalSourceAuthority);
// Provider-native earn/funding product identity used by Capital settlement.
text_type!(EarnProductId);
