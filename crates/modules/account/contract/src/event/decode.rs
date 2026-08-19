use kairos_protocol::generated::kairos::account::v_2 as fb;

use crate::{AccountEvent, ContractError, ContractResult};

pub fn decode_event(bytes: &[u8]) -> ContractResult<AccountEvent<'_>> {
    macro_rules! decode {
        ($check:ident, $root:ident, $variant:ident) => {
            if fb::$check(bytes) {
                return fb::$root(bytes)
                    .map(AccountEvent::$variant)
                    .map_err(|error| ContractError::Invalid(error.to_string()));
            }
        };
    }
    decode!(
        balance_upserted_buffer_has_identifier,
        root_as_balance_upserted,
        BalanceUpserted
    );
    decode!(
        balance_removed_buffer_has_identifier,
        root_as_balance_removed,
        BalanceRemoved
    );
    decode!(
        position_upserted_buffer_has_identifier,
        root_as_position_upserted,
        PositionUpserted
    );
    decode!(
        position_removed_buffer_has_identifier,
        root_as_position_removed,
        PositionRemoved
    );
    decode!(
        valuation_changed_buffer_has_identifier,
        root_as_valuation_changed,
        ValuationChanged
    );
    decode!(
        account_status_changed_buffer_has_identifier,
        root_as_account_status_changed,
        AccountStatusChanged
    );
    decode!(
        observed_order_upserted_buffer_has_identifier,
        root_as_observed_order_upserted,
        ObservedOrderUpserted
    );
    decode!(
        observed_order_removed_buffer_has_identifier,
        root_as_observed_order_removed,
        ObservedOrderRemoved
    );
    Err(ContractError::Invalid(
        "unknown Account v2 event identifier".into(),
    ))
}
