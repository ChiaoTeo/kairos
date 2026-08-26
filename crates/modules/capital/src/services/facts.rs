use kairos_account_contract::AccountClient;
use kairos_primitives::account::AccountId;
use kairos_primitives::decimal::Quantity;
use kairos_primitives::runtime::InstanceIdentity;
use kairos_primitives::time::{Generation, Sequence, UnixNanos};

use crate::{CapitalGroupMember, FundingLocation};

pub(crate) fn read_member_account_observation(
    account: &AccountClient,
    identity: &InstanceIdentity,
    member: &CapitalGroupMember,
) -> Result<crate::CapitalMemberAccountObservation, String> {
    let account_id = member.account_id.as_str();
    let account_current = account
        .indexed_current(
            identity,
            AccountId::new(account_id).map_err(|error| error.to_string())?,
        )
        .map_err(|error| error.to_string())?;
    let snapshot = account_current
        .snapshot()
        .map_err(|error| error.to_string())?;
    let metadata = snapshot.metadata();
    Ok(crate::CapitalMemberAccountObservation {
        broker: member.broker.clone(),
        account_id: member.account_id.clone(),
        account_watermark: Sequence::new(metadata.applied_event_sequence),
        account_observed_at: UnixNanos::new(metadata.committed_at_unix_nanos),
        account_complete: true,
    })
}

pub(crate) fn read_location_facts(
    account: &AccountClient,
    identity: &InstanceIdentity,
    location: &FundingLocation,
    strategy_id: &str,
    risk_snapshot: &kairos_risk_contract::RiskIndexedSnapshot,
    risk_policy_version: Generation,
    risk_watermark: Sequence,
) -> Result<crate::CapitalFacts, String> {
    let account_id = location.account_id.as_str();
    let account_current = account
        .indexed_current(
            identity,
            AccountId::new(account_id).map_err(|error| error.to_string())?,
        )
        .map_err(|error| error.to_string())?;
    let snapshot = account_current
        .snapshot()
        .map_err(|error| error.to_string())?;
    let metadata = snapshot.metadata();
    let segment_values = snapshot.segments();
    let segment = segment_values
        .iter()
        .map(|value| value.segment().map(|current| current.state()))
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| error.to_string())?
        .into_iter()
        .find(|segment| segment.segment_key() == location.segment.as_str())
        .ok_or_else(|| {
            format!(
                "Account '{}' has no Capital segment '{}'",
                location.account_id, location.segment
            )
        })?;
    let observed_available = snapshot
        .balances()
        .iter()
        .map(|value| value.balance())
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| error.to_string())?
        .into_iter()
        .filter(|current| current.segment_key() == location.segment.as_str())
        .map(|current| current.balance())
        .find(|balance| {
            balance.asset_code().unwrap_or(balance.asset_id()) == location.asset.as_str()
        })
        .and_then(|balance| balance.available())
        .map(quantity_from_decimal)
        .transpose()?
        .unwrap_or(Quantity::ZERO);
    let earn_holdings = snapshot
        .earn_holdings()
        .iter()
        .map(|value| value.earn_holding())
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| error.to_string())?
        .into_iter()
        .filter(|current| current.segment_key() == location.segment.as_str())
        .map(|current| current.holding())
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
            .max(metadata.applied_event_sequence),
    );
    let account_complete = segment.completeness()
        == kairos_protocol::generated::kairos::account::v_2::SegmentCompleteness::COMPLETE;
    let policy_values = risk_snapshot.policies();
    let matching_policy_ids = policy_values
        .iter()
        .map(|value| value.policy())
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| error.to_string())?
        .into_iter()
        .filter(|current| {
            let risk_policy = current.policy();
            let scope = risk_policy.scope();
            risk_policy.metric() == kairos_protocol::generated::kairos::risk::v_2::Metric::MARGIN
                && scope.account_id().is_none_or(|value| value == account_id)
                && scope.strategy_id().is_none_or(|value| value == strategy_id)
        })
        .map(|current| current.policy().policy_id().to_owned())
        .collect::<std::collections::BTreeSet<_>>();
    let limit_usage_values = risk_snapshot.limit_usage();
    let risk_capacity = limit_usage_values
        .iter()
        .map(|value| value.limit_usage())
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| error.to_string())?
        .into_iter()
        .filter(|usage| matching_policy_ids.contains(usage.policy_id()))
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
