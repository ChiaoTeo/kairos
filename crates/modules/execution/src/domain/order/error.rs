use kairos_primitives::DomainTypeError;

use super::{CommitmentBasis, ExecutionOrderStatus, Money, OrderId, Quantity, RemoteOrderId};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FillValidationFailure {
    QuantityOrPriceNotPositive,
    FeeNegative,
}

impl std::fmt::Display for FillValidationFailure {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(match self {
            Self::QuantityOrPriceNotPositive => "quantity and price must be positive",
            Self::FeeNegative => "fee must not be negative",
        })
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RouteConstraintFailure {
    Account,
    Segment,
    Instrument,
    Market,
    OrderType,
    Option(&'static str),
    NotReady,
}

impl RouteConstraintFailure {
    const fn code(self) -> &'static str {
        match self {
            Self::Account => "execution.order.route.account",
            Self::Segment => "execution.order.route.segment",
            Self::Instrument => "execution.order.route.instrument",
            Self::Market => "execution.order.route.market",
            Self::OrderType => "execution.order.route.order_type",
            Self::Option(_) => "execution.order.route.option",
            Self::NotReady => "execution.order.route.not_ready",
        }
    }
}

impl std::fmt::Display for RouteConstraintFailure {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Account => formatter.write_str("account mismatch"),
            Self::Segment => formatter.write_str("segment mismatch"),
            Self::Instrument => formatter.write_str("instrument mismatch"),
            Self::Market => formatter.write_str("market mismatch"),
            Self::OrderType => formatter.write_str("order type is unsupported"),
            Self::Option(option) => write!(formatter, "order option {option} is unsupported"),
            Self::NotReady => formatter.write_str("route is not ready"),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum OrderError {
    InvalidSemantic {
        field: &'static str,
        source: DomainTypeError,
    },
    QuantityNotPositive {
        quantity: Quantity,
    },
    CommitmentNotPositive {
        amount: Money,
        remaining_quantity: Quantity,
    },
    DuplicateOrder {
        order_id: OrderId,
    },
    UnknownOrder {
        order_id: String,
    },
    NotPendingAdmission {
        order_id: OrderId,
        status: ExecutionOrderStatus,
    },
    Arithmetic {
        operation: &'static str,
        source: DomainTypeError,
    },
    UnsupportedCommitmentResize {
        order_id: OrderId,
        basis: CommitmentBasis,
    },
    IndeterminateCancelAttempt {
        order_id: OrderId,
    },
    MissingSelectedRoute {
        order_id: OrderId,
    },
    MissingCancelAttempt {
        order_id: OrderId,
    },
    InvalidFill {
        fill_id: String,
        failure: FillValidationFailure,
    },
    TerminalOrder {
        order_id: OrderId,
        status: ExecutionOrderStatus,
    },
    FillExceedsQuantity {
        order_id: OrderId,
        cumulative: Quantity,
        ordered: Quantity,
    },
    RemoteIdentityConflict {
        order_id: OrderId,
        expected: Option<RemoteOrderId>,
        actual: RemoteOrderId,
    },
    UnknownRemoteOrder {
        remote_order_id: String,
    },
    MissingFill {
        fill_id: String,
    },
    MissingOrderForFill {
        fill_id: String,
        order_id: OrderId,
    },
    MissingCommitmentForFill {
        fill_id: String,
        order_id: OrderId,
    },
    MissingExecutionRoute {
        order_id: OrderId,
    },
    RouteNotConfigured {
        route_id: String,
    },
    RouteConstraint {
        route_id: String,
        failure: RouteConstraintFailure,
    },
    UnsupportedTimeInForce {
        value: String,
    },
    ReplacementTotalNotAboveFilled {
        requested_total: Quantity,
        filled_quantity: Quantity,
    },
}

impl OrderError {
    pub const fn code(&self) -> &'static str {
        match self {
            Self::InvalidSemantic { .. } => "execution.order.invalid_semantic",
            Self::QuantityNotPositive { .. } => "execution.order.quantity_not_positive",
            Self::CommitmentNotPositive { .. } => "execution.order.commitment_not_positive",
            Self::DuplicateOrder { .. } => "execution.order.duplicate",
            Self::UnknownOrder { .. } => "execution.order.unknown",
            Self::NotPendingAdmission { .. } => "execution.order.not_pending_admission",
            Self::Arithmetic { .. } => "execution.order.arithmetic",
            Self::UnsupportedCommitmentResize { .. } => {
                "execution.order.unsupported_commitment_resize"
            },
            Self::IndeterminateCancelAttempt { .. } => {
                "execution.order.indeterminate_cancel_attempt"
            },
            Self::MissingSelectedRoute { .. } => "execution.order.missing_selected_route",
            Self::MissingCancelAttempt { .. } => "execution.order.missing_cancel_attempt",
            Self::InvalidFill { .. } => "execution.order.invalid_fill",
            Self::TerminalOrder { .. } => "execution.order.terminal",
            Self::FillExceedsQuantity { .. } => "execution.order.fill_exceeds_quantity",
            Self::RemoteIdentityConflict { .. } => "execution.order.remote_identity_conflict",
            Self::UnknownRemoteOrder { .. } => "execution.order.unknown_remote",
            Self::MissingFill { .. } => "execution.order.missing_fill",
            Self::MissingOrderForFill { .. } => "execution.order.missing_order_for_fill",
            Self::MissingCommitmentForFill { .. } => "execution.order.missing_commitment_for_fill",
            Self::MissingExecutionRoute { .. } => "execution.order.missing_execution_route",
            Self::RouteNotConfigured { .. } => "execution.order.route_not_configured",
            Self::RouteConstraint { failure, .. } => failure.code(),
            Self::UnsupportedTimeInForce { .. } => "execution.order.unsupported_time_in_force",
            Self::ReplacementTotalNotAboveFilled { .. } => {
                "execution.order.replacement_total_not_above_filled"
            },
        }
    }

    pub(crate) fn invalid_semantic(field: &'static str, source: DomainTypeError) -> Self {
        Self::InvalidSemantic { field, source }
    }
}

impl std::fmt::Display for OrderError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidSemantic { field, source } => {
                write!(formatter, "invalid execution order {field}: {source}")
            },
            Self::QuantityNotPositive { .. } => {
                formatter.write_str("execution order quantity must be positive")
            },
            Self::CommitmentNotPositive { .. } => {
                formatter.write_str("order commitment amount and quantity must be positive")
            },
            Self::DuplicateOrder { order_id } => {
                write!(formatter, "order {order_id} already exists")
            },
            Self::UnknownOrder { order_id } => write!(formatter, "unknown order {order_id}"),
            Self::NotPendingAdmission { order_id, status } => {
                write!(
                    formatter,
                    "order {order_id} is not pending admission: {status:?}"
                )
            },
            Self::Arithmetic { operation, source } => {
                write!(formatter, "order {operation} failed: {source}")
            },
            Self::UnsupportedCommitmentResize { .. } => formatter.write_str(
                "contract commitment resizing requires execution-channel-specific semantics",
            ),
            Self::IndeterminateCancelAttempt { .. } => formatter
                .write_str("order has an indeterminate cancel attempt; reconcile before retry"),
            Self::MissingSelectedRoute { .. } => {
                formatter.write_str("order has no durable selected route")
            },
            Self::MissingCancelAttempt { .. } => formatter.write_str("cancel attempt is missing"),
            Self::InvalidFill { fill_id, failure } => {
                write!(formatter, "fill {fill_id} is invalid: {failure}")
            },
            Self::TerminalOrder { order_id, status } => {
                write!(formatter, "order {order_id} is terminal: {status:?}")
            },
            Self::FillExceedsQuantity { order_id, .. } => {
                write!(formatter, "fill exceeds order {order_id} quantity")
            },
            Self::RemoteIdentityConflict { order_id, .. } => {
                write!(formatter, "remote identity conflicts with order {order_id}")
            },
            Self::UnknownRemoteOrder { remote_order_id } => {
                write!(formatter, "unknown remote order {remote_order_id}")
            },
            Self::MissingFill { fill_id } => {
                write!(
                    formatter,
                    "simulated fill {fill_id} is missing from execution state"
                )
            },
            Self::MissingOrderForFill { fill_id, order_id } => {
                write!(
                    formatter,
                    "simulated fill {fill_id} has no execution order {order_id}"
                )
            },
            Self::MissingCommitmentForFill { fill_id, order_id } => write!(
                formatter,
                "simulated fill {fill_id} has no durable commitment for order {order_id}"
            ),
            Self::MissingExecutionRoute { order_id } => {
                write!(formatter, "order {order_id} has no execution route")
            },
            Self::RouteNotConfigured { route_id } => {
                write!(formatter, "execution route {route_id} is not configured")
            },
            Self::RouteConstraint { route_id, failure } => {
                write!(
                    formatter,
                    "execution route {route_id} constraint failed: {failure}"
                )
            },
            Self::UnsupportedTimeInForce { value } => {
                write!(formatter, "unsupported time_in_force: {value}")
            },
            Self::ReplacementTotalNotAboveFilled { .. } => formatter
                .write_str("replacement total quantity must exceed the original filled quantity"),
        }
    }
}

impl std::error::Error for OrderError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::InvalidSemantic { source, .. } | Self::Arithmetic { source, .. } => Some(source),
            _ => None,
        }
    }
}
