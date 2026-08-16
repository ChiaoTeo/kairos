use kairos_protocol::generated::kairos::account::v_2 as fb;

pub enum AccountEvent<'a> {
    BalanceUpserted(fb::BalanceUpserted<'a>),
    BalanceRemoved(fb::BalanceRemoved<'a>),
    PositionUpserted(fb::PositionUpserted<'a>),
    PositionRemoved(fb::PositionRemoved<'a>),
    ValuationChanged(fb::ValuationChanged<'a>),
    AccountStatusChanged(fb::AccountStatusChanged<'a>),
    ObservedOrderUpserted(fb::ObservedOrderUpserted<'a>),
    ObservedOrderRemoved(fb::ObservedOrderRemoved<'a>),
}
