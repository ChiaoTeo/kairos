use std::path::Path;

use kairos_account_contract::{AccountViewKey, AccountViewKind, AccountViewReader};
use kairos_primitives::decimal::Quantity;
use kairos_primitives::time::{Generation, Sequence, UnixNanos};

use crate::{CapitalGroupMember, FundingLocation};

pub(crate) fn read_member_account_observation(
    snapshot_root: &Path,
    member: &CapitalGroupMember,
) -> Result<crate::CapitalMemberAccountObservation, String> {
    let account_id = member.account_id.as_str();
    let account_reader = AccountViewReader::open(
        snapshot_root,
        AccountViewKey::new(
            format!("account:{account_id}"),
            account_id,
            AccountViewKind::Current,
        )
        .map_err(|error| error.to_string())?,
    )
    .map_err(|error| error.to_string())?;
    let account_frame = account_reader.read().map_err(|error| error.to_string())?;
    let account_root = account_frame
        .account_current()
        .map_err(|error| error.to_string())?;
    if account_root.account_id() != account_id {
        return Err(format!(
            "Account view identity '{}' does not match Capital member '{account_id}'",
            account_root.account_id()
        ));
    }
    let metadata = account_root.metadata();
    Ok(crate::CapitalMemberAccountObservation {
        broker: member.broker.clone(),
        account_id: member.account_id.clone(),
        account_watermark: Sequence::new(
            account_frame
                .envelope_metadata()
                .applied_event_sequence
                .max(metadata.applied_revision().unwrap_or(0)),
        ),
        account_observed_at: UnixNanos::new(metadata.as_of_unix_nanos()),
        account_complete: metadata.completeness()
            == kairos_protocol::generated::kairos::common::v_2::ViewCompleteness::COMPLETE,
    })
}

pub(crate) fn read_location_facts(
    snapshot_root: &Path,
    location: &FundingLocation,
    strategy_id: &str,
    risk_state: kairos_protocol::generated::kairos::risk::v_2::RiskLatestState<'_>,
    risk_policy_version: Generation,
    risk_watermark: Sequence,
) -> Result<crate::CapitalFacts, String> {
    let account_id = location.account_id.as_str();
    let account_reader = AccountViewReader::open(
        snapshot_root,
        AccountViewKey::new(
            format!("account:{account_id}"),
            account_id,
            AccountViewKind::Current,
        )
        .map_err(|error| error.to_string())?,
    )
    .map_err(|error| error.to_string())?;
    let account_frame = account_reader.read().map_err(|error| error.to_string())?;
    let account_root = account_frame
        .account_current()
        .map_err(|error| error.to_string())?;
    let segment = account_root
        .segments()
        .iter()
        .find(|segment| segment.segment_key() == location.segment.as_str())
        .ok_or_else(|| {
            format!(
                "Account '{}' has no Capital segment '{}'",
                location.account_id, location.segment
            )
        })?;
    let observed_available = segment
        .balances()
        .iter()
        .find(|balance| {
            balance.asset_code().unwrap_or(balance.asset_id()) == location.asset.as_str()
        })
        .and_then(|balance| balance.available())
        .map(quantity_from_decimal)
        .transpose()?
        .unwrap_or(Quantity::ZERO);
    let earn_holdings = segment
        .earn_holdings()
        .iter()
        .filter(|holding| holding.asset() == location.asset.as_str())
        .filter_map(|holding| {
            holding.redeemable().map(|redeemable| {
                quantity_from_decimal(redeemable).and_then(|redeemable_amount| {
                    Ok(crate::CapitalEarnHoldingFact {
                        product_id: holding.product_id().to_owned(),
                        principal: quantity_from_decimal(holding.principal())?,
                        redeemable_amount,
                        immediately_redeemable: holding.liquidity()
                            == kairos_protocol::generated::kairos::account::v_2::EarnLiquidity::IMMEDIATE,
                        active: holding.state()
                            == kairos_protocol::generated::kairos::account::v_2::EarnHoldingState::ACTIVE,
                    })
                })
            })
        })
        .collect::<Result<Vec<_>, _>>()?;
    let account_watermark = Sequence::new(
        segment
            .snapshot_watermark()
            .max(segment.event_watermark())
            .max(account_frame.envelope_metadata().applied_event_sequence),
    );
    let account_complete = account_root.metadata().completeness()
        == kairos_protocol::generated::kairos::common::v_2::ViewCompleteness::COMPLETE
        && segment.completeness()
            == kairos_protocol::generated::kairos::account::v_2::SegmentCompleteness::COMPLETE;
    let risk_capacity = risk_state
        .limits()
        .iter()
        .filter(|usage| {
            let risk_policy = usage.policy();
            let scope = risk_policy.scope();
            risk_policy.metric() == kairos_protocol::generated::kairos::risk::v_2::Metric::MARGIN
                && scope.account_id().is_none_or(|value| value == account_id)
                && scope.strategy_id().is_none_or(|value| value == strategy_id)
        })
        .map(|usage| quantity_from_decimal(usage.available()))
        .collect::<Result<Vec<_>, _>>()?
        .into_iter()
        .min()
        .unwrap_or(Quantity::ZERO);
    Ok(crate::CapitalFacts {
        destination: location.clone(),
        observed_available,
        account_watermark,
        account_observed_at: UnixNanos::new(segment.observed_at_unix_nanos()),
        account_complete,
        risk_capacity,
        risk_policy_version,
        risk_watermark,
        earn_holdings,
    })
}

fn quantity_from_decimal(
    value: &kairos_protocol::generated::kairos::common::v_2::Decimal64,
) -> Result<Quantity, String> {
    Quantity::new(value.mantissa(), value.scale()).map_err(|error| error.to_string())
}
