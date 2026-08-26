use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use kairos_indexed_view::{
    EnvironmentOptions, IndexedViewIdentity, IndexedViewReader, MetadataSnapshot, PrefixRequest,
    SchemaDescriptor, SchemaSet, environment_path,
};
use kairos_primitives::capital::CapitalGroupId;
use kairos_primitives::runtime::InstanceIdentity;
use kairos_protocol::generated::kairos::capital::v_2 as fb;

use crate::{ContractError, ContractResult, FundingLocation};

pub const CAPITAL_STATE_DATABASE: &str = "state";
pub const CAPITAL_OBJECTIVES_DATABASE: &str = "objectives";
pub const CAPITAL_DEMANDS_DATABASE: &str = "demands";
pub const CAPITAL_POLICIES_DATABASE: &str = "policies";
pub const CAPITAL_FACTS_DATABASE: &str = "facts";
pub const CAPITAL_AVAILABILITY_DATABASE: &str = "availability";
pub const CAPITAL_ROUTES_DATABASE: &str = "routes";
pub const CAPITAL_PLANS_DATABASE: &str = "plans";
pub const CAPITAL_RESERVATIONS_DATABASE: &str = "reservations";
pub const CAPITAL_OPERATIONS_DATABASE: &str = "operations";
pub const CAPITAL_ALERTS_DATABASE: &str = "alerts";
pub const CAPITAL_RESOURCE_EPOCH: u64 = 1;
pub const CAPITAL_MAP_SIZE: usize = 256 * 1024 * 1024;
const KEY_VERSION: u8 = 1;
const ALL_VALUES_PREFIX: [u8; 1] = [KEY_VERSION];

const DATABASES: [&str; 11] = [
    CAPITAL_STATE_DATABASE,
    CAPITAL_OBJECTIVES_DATABASE,
    CAPITAL_DEMANDS_DATABASE,
    CAPITAL_POLICIES_DATABASE,
    CAPITAL_FACTS_DATABASE,
    CAPITAL_AVAILABILITY_DATABASE,
    CAPITAL_ROUTES_DATABASE,
    CAPITAL_PLANS_DATABASE,
    CAPITAL_RESERVATIONS_DATABASE,
    CAPITAL_OPERATIONS_DATABASE,
    CAPITAL_ALERTS_DATABASE,
];

pub fn capital_indexed_schema_set() -> SchemaSet {
    SchemaSet::new(DATABASES.map(|database| {
        SchemaDescriptor::new(
            database,
            1,
            match database {
                CAPITAL_STATE_DATABASE => "CSM3",
                CAPITAL_OBJECTIVES_DATABASE => "CFO3",
                CAPITAL_DEMANDS_DATABASE => "CDM3",
                CAPITAL_POLICIES_DATABASE => "CPC3",
                CAPITAL_FACTS_DATABASE => "CFC3",
                CAPITAL_AVAILABILITY_DATABASE => "CAV3",
                CAPITAL_ROUTES_DATABASE => "CRT3",
                CAPITAL_PLANS_DATABASE => "CPL3",
                CAPITAL_RESERVATIONS_DATABASE => "CRS3",
                CAPITAL_OPERATIONS_DATABASE => "COP3",
                CAPITAL_ALERTS_DATABASE => "CAL3",
                _ => unreachable!("all Capital databases have a dedicated value root"),
            },
            1,
        )
        .expect("static Capital schema")
    }))
    .expect("static Capital databases are unique")
}

pub fn capital_indexed_identity(
    identity: &InstanceIdentity,
    group_id: &CapitalGroupId,
    producer_incarnation: u64,
) -> IndexedViewIdentity {
    IndexedViewIdentity::new(
        identity.workspace_id.to_string(),
        identity.launch_id().map(ToString::to_string),
        identity.instance_id().map(ToString::to_string),
        "Capital",
        format!("capital-{group_id}"),
        CAPITAL_RESOURCE_EPOCH,
        producer_incarnation,
        capital_indexed_schema_set(),
    )
    .expect("validated Capital identity produces indexed-view identity")
}

pub fn capital_indexed_environment_path(
    root: impl AsRef<Path>,
    identity: &InstanceIdentity,
    group_id: &CapitalGroupId,
) -> ContractResult<PathBuf> {
    environment_path(root, &capital_indexed_identity(identity, group_id, 1))
        .map_err(|error| ContractError::Transport(error.to_string()))
}

pub fn capital_indexed_key(parts: &[&str]) -> ContractResult<Vec<u8>> {
    if parts.is_empty() {
        return Err(ContractError::Invalid(
            "Capital indexed key requires components".into(),
        ));
    }
    let mut key = vec![KEY_VERSION];
    for part in parts {
        if part.is_empty() || part.trim() != *part || part.as_bytes().contains(&0) {
            return Err(ContractError::Invalid(
                "invalid Capital indexed key component".into(),
            ));
        }
        let length: u16 = part.len().try_into().map_err(|_| {
            ContractError::Invalid("Capital indexed key component is too long".into())
        })?;
        key.extend_from_slice(&length.to_be_bytes());
        key.extend_from_slice(part.as_bytes());
    }
    Ok(key)
}

pub fn location_key(location: &FundingLocation) -> ContractResult<Vec<u8>> {
    capital_indexed_key(&[
        location.broker.as_str(),
        location.account_id.as_str(),
        location.segment.as_str(),
        location.asset.as_str(),
    ])
}

pub struct CapitalIndexedView {
    reader: IndexedViewReader,
    group_id: CapitalGroupId,
}

impl CapitalIndexedView {
    pub fn open(
        root: impl AsRef<Path>,
        identity: &InstanceIdentity,
        group_id: CapitalGroupId,
    ) -> ContractResult<Self> {
        let options = EnvironmentOptions::new(
            capital_indexed_environment_path(root, identity, &group_id)?,
            CAPITAL_MAP_SIZE,
        )
        .map_err(|error| ContractError::Transport(error.to_string()))?;
        let reader =
            IndexedViewReader::open(&options, capital_indexed_identity(identity, &group_id, 1))
                .map_err(|error| ContractError::Transport(error.to_string()))?;
        Ok(Self { reader, group_id })
    }

    pub fn snapshot(&self) -> ContractResult<CapitalIndexedSnapshot> {
        let requests = DATABASES.map(|database| PrefixRequest {
            database,
            prefix: &ALL_VALUES_PREFIX,
            limit: usize::MAX,
        });
        let snapshot = self
            .reader
            .snapshot(&requests)
            .map_err(|error| ContractError::Transport(error.to_string()))?;
        if snapshot.metadata.rebuild_state != kairos_indexed_view::RebuildState::Ready {
            return Err(ContractError::Transport(
                "Capital indexed current view is not ready".into(),
            ));
        }
        Ok(CapitalIndexedSnapshot {
            metadata: snapshot.metadata,
            group_id: self.group_id.clone(),
            rows: snapshot.rows,
        })
    }
}

pub struct CapitalIndexedSnapshot {
    metadata: MetadataSnapshot,
    group_id: CapitalGroupId,
    rows: BTreeMap<String, Vec<(Vec<u8>, Vec<u8>)>>,
}

pub enum CapitalIndexedEntity<'a> {
    Objective(fb::FundingObjective<'a>),
    Demand(fb::CapitalDemand<'a>),
    Policy(fb::CapitalPolicy<'a>),
    Facts(fb::CapitalFacts<'a>),
    Availability(fb::CapitalAvailability<'a>),
    Route(fb::CapitalRoute<'a>),
    Plan(fb::CapitalPlan<'a>),
    Reservation(fb::CapitalReservation<'a>),
    Operation(fb::CapitalOperation<'a>),
    Alert(fb::CapitalAlert<'a>),
}

macro_rules! capital_entity_accessors {
    ($($method:ident => $variant:ident($value:ty)),+ $(,)?) => {
        impl<'a> CapitalIndexedEntity<'a> {
            $(
                pub fn $method(self) -> Option<$value> {
                    match self {
                        Self::$variant(value) => Some(value),
                        _ => None,
                    }
                }
            )+
        }
    };
}

capital_entity_accessors! {
    objective => Objective(fb::FundingObjective<'a>),
    demand => Demand(fb::CapitalDemand<'a>),
    policy => Policy(fb::CapitalPolicy<'a>),
    facts => Facts(fb::CapitalFacts<'a>),
    availability => Availability(fb::CapitalAvailability<'a>),
    route => Route(fb::CapitalRoute<'a>),
    plan => Plan(fb::CapitalPlan<'a>),
    reservation => Reservation(fb::CapitalReservation<'a>),
    operation => Operation(fb::CapitalOperation<'a>),
    alert => Alert(fb::CapitalAlert<'a>),
}

macro_rules! decode_capital_current {
    ($bytes:expr, $group_id:expr, $identifier:ident, $decode:ident, $variant:ident, $label:literal) => {{
        if !fb::$identifier($bytes) {
            return Err(ContractError::Invalid(concat!("expected ", $label).into()));
        }
        let root =
            fb::$decode($bytes).map_err(|error| ContractError::Invalid(error.to_string()))?;
        if root.capital_group_id() != $group_id.as_str() {
            return Err(ContractError::Invalid(
                "Capital group identity mismatch".into(),
            ));
        }
        CapitalIndexedEntity::$variant(root.value())
    }};
}

impl CapitalIndexedSnapshot {
    pub fn metadata(&self) -> &MetadataSnapshot {
        &self.metadata
    }

    pub fn state(&self) -> ContractResult<fb::CapitalStateCurrent<'_>> {
        let rows = self
            .rows
            .get(CAPITAL_STATE_DATABASE)
            .map(Vec::as_slice)
            .unwrap_or_default();
        if rows.len() != 1 {
            return Err(ContractError::Invalid(
                "Capital snapshot must contain one state".into(),
            ));
        }
        let (_, bytes) = &rows[0];
        if !fb::capital_state_current_buffer_has_identifier(bytes) {
            return Err(ContractError::Invalid(
                "expected CSM3 CapitalStateCurrent".into(),
            ));
        }
        let value = fb::root_as_capital_state_current(bytes)
            .map_err(|error| ContractError::Invalid(error.to_string()))?;
        if value.capital_group_id() != self.group_id.as_str() {
            return Err(ContractError::Invalid(
                "Capital group identity mismatch".into(),
            ));
        }
        Ok(value)
    }

    pub fn entities(&self, database: &str) -> ContractResult<Vec<CapitalIndexedEntity<'_>>> {
        if database == CAPITAL_STATE_DATABASE || !DATABASES.contains(&database) {
            return Err(ContractError::Invalid(
                "invalid Capital entity database".into(),
            ));
        }
        self.rows
            .get(database)
            .into_iter()
            .flatten()
            .map(|(key, bytes)| {
                let value = match database {
                    CAPITAL_OBJECTIVES_DATABASE => decode_capital_current!(
                        bytes,
                        self.group_id,
                        capital_objective_current_buffer_has_identifier,
                        root_as_capital_objective_current,
                        Objective,
                        "CFO3 CapitalObjectiveCurrent"
                    ),
                    CAPITAL_DEMANDS_DATABASE => decode_capital_current!(
                        bytes,
                        self.group_id,
                        capital_demand_current_buffer_has_identifier,
                        root_as_capital_demand_current,
                        Demand,
                        "CDM3 CapitalDemandCurrent"
                    ),
                    CAPITAL_POLICIES_DATABASE => decode_capital_current!(
                        bytes,
                        self.group_id,
                        capital_policy_current_buffer_has_identifier,
                        root_as_capital_policy_current,
                        Policy,
                        "CPC3 CapitalPolicyCurrent"
                    ),
                    CAPITAL_FACTS_DATABASE => decode_capital_current!(
                        bytes,
                        self.group_id,
                        capital_facts_current_buffer_has_identifier,
                        root_as_capital_facts_current,
                        Facts,
                        "CFC3 CapitalFactsCurrent"
                    ),
                    CAPITAL_AVAILABILITY_DATABASE => decode_capital_current!(
                        bytes,
                        self.group_id,
                        capital_availability_current_buffer_has_identifier,
                        root_as_capital_availability_current,
                        Availability,
                        "CAV3 CapitalAvailabilityCurrent"
                    ),
                    CAPITAL_ROUTES_DATABASE => decode_capital_current!(
                        bytes,
                        self.group_id,
                        capital_route_current_buffer_has_identifier,
                        root_as_capital_route_current,
                        Route,
                        "CRT3 CapitalRouteCurrent"
                    ),
                    CAPITAL_PLANS_DATABASE => decode_capital_current!(
                        bytes,
                        self.group_id,
                        capital_plan_current_buffer_has_identifier,
                        root_as_capital_plan_current,
                        Plan,
                        "CPL3 CapitalPlanCurrent"
                    ),
                    CAPITAL_RESERVATIONS_DATABASE => decode_capital_current!(
                        bytes,
                        self.group_id,
                        capital_reservation_current_buffer_has_identifier,
                        root_as_capital_reservation_current,
                        Reservation,
                        "CRS3 CapitalReservationCurrent"
                    ),
                    CAPITAL_OPERATIONS_DATABASE => decode_capital_current!(
                        bytes,
                        self.group_id,
                        capital_operation_current_buffer_has_identifier,
                        root_as_capital_operation_current,
                        Operation,
                        "COP3 CapitalOperationCurrent"
                    ),
                    CAPITAL_ALERTS_DATABASE => decode_capital_current!(
                        bytes,
                        self.group_id,
                        capital_alert_current_buffer_has_identifier,
                        root_as_capital_alert_current,
                        Alert,
                        "CAL3 CapitalAlertCurrent"
                    ),
                    _ => unreachable!("database checked above"),
                };
                let expected = match &value {
                    CapitalIndexedEntity::Objective(value) => vec![value.objective_id()],
                    CapitalIndexedEntity::Demand(value) => vec![value.demand_id()],
                    CapitalIndexedEntity::Policy(value) => location_parts(value.destination()),
                    CapitalIndexedEntity::Facts(value) => location_parts(value.destination()),
                    CapitalIndexedEntity::Availability(value) => {
                        location_parts(value.destination())
                    },
                    CapitalIndexedEntity::Route(value) => vec![value.route_id()],
                    CapitalIndexedEntity::Plan(value) => vec![value.plan_id()],
                    CapitalIndexedEntity::Reservation(value) => vec![value.reservation_id()],
                    CapitalIndexedEntity::Operation(value) => vec![value.operation_id()],
                    CapitalIndexedEntity::Alert(value) => vec![value.alert_id()],
                };
                if decode_key_components(key)? != expected {
                    return Err(ContractError::Invalid(
                        "Capital indexed key/value identity mismatch".into(),
                    ));
                }
                Ok(value)
            })
            .collect()
    }
}

fn location_parts(value: fb::FundingLocation<'_>) -> Vec<&str> {
    vec![
        value.broker(),
        value.account_id(),
        value.segment(),
        value.asset(),
    ]
}

fn decode_key_components(key: &[u8]) -> ContractResult<Vec<&str>> {
    if key.first() != Some(&KEY_VERSION) {
        return Err(ContractError::Invalid(
            "invalid Capital indexed key version".into(),
        ));
    }
    let mut offset = 1;
    let mut parts = Vec::new();
    while offset < key.len() {
        let length_bytes = key
            .get(offset..offset + 2)
            .ok_or_else(|| ContractError::Invalid("truncated Capital indexed key".into()))?;
        let length = u16::from_be_bytes([length_bytes[0], length_bytes[1]]) as usize;
        offset += 2;
        let bytes = key
            .get(offset..offset + length)
            .ok_or_else(|| ContractError::Invalid("truncated Capital indexed key".into()))?;
        parts.push(
            std::str::from_utf8(bytes)
                .map_err(|error| ContractError::Invalid(error.to_string()))?,
        );
        offset += length;
    }
    Ok(parts)
}
