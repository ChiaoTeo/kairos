use kairos_protocol::generated::kairos::execution::v_2 as fb;
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
