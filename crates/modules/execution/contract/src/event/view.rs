use kairos_protocol::generated::kairos::execution::v_2 as fb;
use kairos_protocol::{BorrowedEventView, BusinessEventKind};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ExecutionEventKind {
    IntentAccepted,
    IntentRejected,
    IntentLifecycleChanged,
    PlanCreated,
    OrderSubmitted,
    OrderAccepted,
    OrderRejected,
    OrderCanceled,
    OrderExpired,
    FillRecorded,
}

impl ExecutionEventKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::IntentAccepted => "intent_accepted",
            Self::IntentRejected => "intent_rejected",
            Self::IntentLifecycleChanged => "intent_lifecycle_changed",
            Self::PlanCreated => "plan_created",
            Self::OrderSubmitted => "order_submitted",
            Self::OrderAccepted => "order_accepted",
            Self::OrderRejected => "order_rejected",
            Self::OrderCanceled => "order_canceled",
            Self::OrderExpired => "order_expired",
            Self::FillRecorded => "fill_recorded",
        }
    }
}

impl BusinessEventKind for ExecutionEventKind {
    fn as_str(self) -> &'static str {
        self.as_str()
    }
}

pub enum ExecutionEvent<'a> {
    IntentAccepted(fb::IntentAccepted<'a>),
    IntentRejected(fb::IntentRejected<'a>),
    IntentLifecycleChanged(fb::IntentLifecycleChanged<'a>),
    PlanCreated(fb::PlanCreated<'a>),
    OrderSubmitted(fb::OrderSubmitted<'a>),
    OrderAccepted(fb::OrderAccepted<'a>),
    OrderRejected(fb::OrderRejected<'a>),
    OrderCanceled(fb::OrderCanceled<'a>),
    OrderExpired(fb::OrderExpired<'a>),
    FillRecorded(fb::FillRecorded<'a>),
}

pub type ExecutionEventView<'a> = ExecutionEvent<'a>;

impl<'a> ExecutionEvent<'a> {
    pub fn kind(&self) -> ExecutionEventKind {
        <Self as BorrowedEventView<'a>>::kind(self)
    }

    pub fn metadata(&self) -> kairos_protocol::generated::kairos::common::v_2::EventMetadata<'a> {
        <Self as BorrowedEventView<'a>>::metadata(self)
    }
}

impl<'a> BorrowedEventView<'a> for ExecutionEvent<'a> {
    type Kind = ExecutionEventKind;

    fn kind(&self) -> Self::Kind {
        match self {
            Self::IntentAccepted(_) => ExecutionEventKind::IntentAccepted,
            Self::IntentRejected(_) => ExecutionEventKind::IntentRejected,
            Self::IntentLifecycleChanged(_) => ExecutionEventKind::IntentLifecycleChanged,
            Self::PlanCreated(_) => ExecutionEventKind::PlanCreated,
            Self::OrderSubmitted(_) => ExecutionEventKind::OrderSubmitted,
            Self::OrderAccepted(_) => ExecutionEventKind::OrderAccepted,
            Self::OrderRejected(_) => ExecutionEventKind::OrderRejected,
            Self::OrderCanceled(_) => ExecutionEventKind::OrderCanceled,
            Self::OrderExpired(_) => ExecutionEventKind::OrderExpired,
            Self::FillRecorded(_) => ExecutionEventKind::FillRecorded,
        }
    }

    fn metadata(&self) -> kairos_protocol::generated::kairos::common::v_2::EventMetadata<'a> {
        match self {
            Self::IntentAccepted(value) => value.metadata(),
            Self::IntentRejected(value) => value.metadata(),
            Self::IntentLifecycleChanged(value) => value.metadata(),
            Self::PlanCreated(value) => value.metadata(),
            Self::OrderSubmitted(value) => value.metadata(),
            Self::OrderAccepted(value) => value.metadata(),
            Self::OrderRejected(value) => value.metadata(),
            Self::OrderCanceled(value) => value.metadata(),
            Self::OrderExpired(value) => value.metadata(),
            Self::FillRecorded(value) => value.metadata(),
        }
    }
}
