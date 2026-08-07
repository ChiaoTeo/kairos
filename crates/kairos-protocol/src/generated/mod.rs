// Generated FlatBuffers modules are kept as independent message types.
#![allow(unused_imports)]

pub mod kairos {
    pub mod common {
        pub mod v_1 {
            mod decimal_64_generated;
            pub use decimal_64_generated::*;
            mod message_header_generated;
            pub use message_header_generated::*;
            mod order_type_generated;
            pub use order_type_generated::*;
            mod side_generated;
            pub use side_generated::*;
            mod snapshot_header_generated;
            pub use snapshot_header_generated::*;
        }
    }

    pub mod reference {
        pub use super::common;
        pub mod v_1 {
            mod asset_generated;
            pub use asset_generated::*;
            mod entity_generated;
            pub use entity_generated::*;
            mod financial_product_generated;
            pub use financial_product_generated::*;
            mod instrument_generated;
            pub use instrument_generated::*;
            mod listing_generated;
            pub use listing_generated::*;
            mod market_generated;
            pub use market_generated::*;
            mod reference_changed_generated;
            pub use reference_changed_generated::*;
            mod venue_generated;
            pub use venue_generated::*;
            mod catalog_generated;
            pub use catalog_generated::*;
            mod catalog_snapshot_generated;
            pub use catalog_snapshot_generated::*;
            mod lifecycle_event_generated;
            pub use lifecycle_event_generated::*;
            mod lifecycle_generated;
            pub use lifecycle_generated::*;
            mod lifecycle_snapshot_generated;
            pub use lifecycle_snapshot_generated::*;
            mod markets_generated;
            pub use markets_generated::*;
            mod markets_snapshot_generated;
            pub use markets_snapshot_generated::*;
        }
    }

    pub mod market {
        pub use super::common;
        pub mod v_1 {
            mod bar_generated;
            pub use bar_generated::*;
            mod greeks_generated;
            pub use greeks_generated::*;
            mod quote_generated;
            pub use quote_generated::*;
            mod quote_message_generated;
            pub use quote_message_generated::*;
            mod rate_generated;
            pub use rate_generated::*;
            mod trade_generated;
            pub use trade_generated::*;
            mod trade_message_generated;
            pub use trade_message_generated::*;
            mod market_data_generated;
            pub use market_data_generated::*;
            mod market_data_snapshot_generated;
            pub use market_data_snapshot_generated::*;
            mod market_history_generated;
            pub use market_history_generated::*;
            mod market_history_snapshot_generated;
            pub use market_history_snapshot_generated::*;
            mod order_book_generated;
            pub use order_book_generated::*;
            mod order_book_level_generated;
            pub use order_book_level_generated::*;
            mod order_book_snapshot_generated;
            pub use order_book_snapshot_generated::*;
            mod order_books_generated;
            pub use order_books_generated::*;
            mod subscription_generated;
            pub use subscription_generated::*;
            mod subscriptions_generated;
            pub use subscriptions_generated::*;
            mod subscriptions_snapshot_generated;
            pub use subscriptions_snapshot_generated::*;
        }
    }

    pub mod account {
        pub use super::common;
        pub mod v_1 {
            mod account_generated;
            pub use account_generated::*;
            mod balance_generated;
            pub use balance_generated::*;
            mod position_generated;
            pub use position_generated::*;
            mod open_order_generated;
            pub use open_order_generated::*;
            mod accounts_generated;
            pub use accounts_generated::*;
            mod accounts_snapshot_generated;
            pub use accounts_snapshot_generated::*;
            mod equity_observation_generated;
            pub use equity_observation_generated::*;
            mod equity_observations_generated;
            pub use equity_observations_generated::*;
            mod equity_snapshot_generated;
            pub use equity_snapshot_generated::*;
        }
    }

    pub mod execution {
        pub use super::common;
        pub mod v_1 {
            mod fill_generated;
            pub use fill_generated::*;
            mod order_generated;
            pub use order_generated::*;
            mod order_filled_generated;
            pub use order_filled_generated::*;
            mod order_filled_message_generated;
            pub use order_filled_message_generated::*;
            mod order_intent_generated;
            pub use order_intent_generated::*;
            mod order_intent_message_generated;
            pub use order_intent_message_generated::*;
            mod fills_generated;
            pub use fills_generated::*;
            mod fills_snapshot_generated;
            pub use fills_snapshot_generated::*;
            mod orders_generated;
            pub use orders_generated::*;
            mod orders_snapshot_generated;
            pub use orders_snapshot_generated::*;
        }
    }

    pub mod intent {
        pub use super::common;
        pub mod v_1 {
            mod intent_generated;
            pub use intent_generated::*;
            mod intents_generated;
            pub use intents_generated::*;
            mod intent_snapshot_generated;
            pub use intent_snapshot_generated::*;
        }
    }

    pub mod risk {
        pub use super::common;
        pub mod v_1 {
            mod allocation_generated;
            pub use allocation_generated::*;
            mod assess_risk_request_generated;
            pub use assess_risk_request_generated::*;
            mod budget_generated;
            pub use budget_generated::*;
            mod budget_ref_generated;
            pub use budget_ref_generated::*;
            mod consume_reservation_request_generated;
            pub use consume_reservation_request_generated::*;
            mod release_reservation_request_generated;
            pub use release_reservation_request_generated::*;
            mod reservation_event_generated;
            pub use reservation_event_generated::*;
            mod reservation_generated;
            pub use reservation_generated::*;
            mod reserve_risk_request_generated;
            pub use reserve_risk_request_generated::*;
            mod risk_assessment_result_generated;
            pub use risk_assessment_result_generated::*;
            mod risk_generated;
            pub use risk_generated::*;
            mod risk_snapshot_generated;
            pub use risk_snapshot_generated::*;
            mod usage_generated;
            pub use usage_generated::*;
        }
    }

    pub mod system {
        pub use super::common;
        pub mod v_1 {
            mod actor_health_generated;
            pub use actor_health_generated::*;
            mod alert_generated;
            pub use alert_generated::*;
            mod alerts_generated;
            pub use alerts_generated::*;
            mod alerts_snapshot_generated;
            pub use alerts_snapshot_generated::*;
            mod connection_health_generated;
            pub use connection_health_generated::*;
            mod domain_freshness_generated;
            pub use domain_freshness_generated::*;
            mod freshness_generated;
            pub use freshness_generated::*;
            mod freshness_snapshot_generated;
            pub use freshness_snapshot_generated::*;
            mod health_snapshot_generated;
            pub use health_snapshot_generated::*;
            mod operation_generated;
            pub use operation_generated::*;
            mod operations_generated;
            pub use operations_generated::*;
            mod operations_snapshot_generated;
            pub use operations_snapshot_generated::*;
            mod system_health_generated;
            pub use system_health_generated::*;
        }
    }
}
