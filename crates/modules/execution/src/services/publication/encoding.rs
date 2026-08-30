use kairos_primitives::decimal::{Money, Quantity};
use kairos_primitives::time::UnixNanos;

use super::*;

pub(super) fn encode_dependency_evidence<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    watermarks: &crate::domain::DependencyWatermarks,
) -> flatbuffers::WIPOffset<
    flatbuffers::Vector<'a, flatbuffers::ForwardsUOffset<common_fb::EvidenceRef<'a>>>,
> {
    let mut values = Vec::new();
    for (account_id, watermark) in &watermarks.account {
        let snapshot_id = builder.create_string(&format!("account:{account_id}"));
        values.push(common_fb::EvidenceRef::create(
            builder,
            &common_fb::EvidenceRefArgs {
                owner: common_fb::DependencyOwner::ACCOUNT,
                kind: common_fb::EvidenceKind::CURRENT_VIEW,
                snapshot_id: Some(snapshot_id),
                generation: Some(watermark.generation.get()),
                sequence: Some(watermark.event_sequence.get()),
                ..Default::default()
            },
        ));
    }
    for (owner, snapshot_id_value, watermark) in [
        (
            common_fb::DependencyOwner::MARKET,
            "market",
            watermarks.market.as_ref(),
        ),
        (
            common_fb::DependencyOwner::REFERENCE,
            "reference",
            watermarks.reference.as_ref(),
        ),
        (
            common_fb::DependencyOwner::RISK,
            "risk",
            watermarks.risk.as_ref(),
        ),
    ] {
        let Some(watermark) = watermark else {
            continue;
        };
        let snapshot_id = builder.create_string(snapshot_id_value);
        values.push(common_fb::EvidenceRef::create(
            builder,
            &common_fb::EvidenceRefArgs {
                owner,
                kind: common_fb::EvidenceKind::CURRENT_VIEW,
                snapshot_id: Some(snapshot_id),
                generation: Some(watermark.generation.get()),
                sequence: Some(watermark.event_sequence.get()),
                ..Default::default()
            },
        ));
    }
    builder.create_vector(&values)
}

pub(super) fn active_order_status(status: ExecutionOrderStatus) -> bool {
    matches!(
        status,
        ExecutionOrderStatus::Pending
            | ExecutionOrderStatus::Submitting
            | ExecutionOrderStatus::Accepted
            | ExecutionOrderStatus::PartiallyFilled
            | ExecutionOrderStatus::CancelRequested
            | ExecutionOrderStatus::Unknown
    )
}

pub(super) fn active_risk_reservation_status(status: RiskReservationSagaStatus) -> bool {
    matches!(
        status,
        RiskReservationSagaStatus::AuthorizePending
            | RiskReservationSagaStatus::Active
            | RiskReservationSagaStatus::ResizePending
            | RiskReservationSagaStatus::ReleasePending
            | RiskReservationSagaStatus::ConsumePending
            | RiskReservationSagaStatus::Uncertain
    )
}

pub(crate) fn encode_indexed_current(
    snapshot: &ExecutionCurrentView,
) -> Result<std::collections::BTreeMap<(String, Vec<u8>), Vec<u8>>, String> {
    use kairos_execution_contract::{
        ALGORITHM_RUNS_DATABASE, COMMITMENTS_DATABASE, INTENTS_DATABASE, ORDERS_DATABASE,
        RISK_RESERVATIONS_DATABASE, UNKNOWN_REMOTE_ORDERS_DATABASE, indexed_entity_key,
    };

    let active_intent_ids = snapshot
        .intents
        .iter()
        .filter(|intent| active_intent_status(intent.status))
        .map(|intent| intent.intent.intent_id.clone())
        .collect::<std::collections::BTreeSet<_>>();
    let mut values = std::collections::BTreeMap::new();
    for order in snapshot
        .orders
        .iter()
        .filter(|order| active_order_status(order.status))
    {
        insert_indexed_value(
            &mut values,
            ORDERS_DATABASE,
            order.order_id.as_str(),
            encode_order_current(order)?,
        )?;
    }
    for intent in snapshot
        .intents
        .iter()
        .filter(|intent| active_intent_ids.contains(&intent.intent.intent_id))
    {
        insert_indexed_value(
            &mut values,
            INTENTS_DATABASE,
            intent.intent.intent_id.as_str(),
            encode_intent_current(intent)?,
        )?;
    }
    for run in snapshot
        .algorithm_runs
        .iter()
        .filter(|run| active_intent_ids.contains(&run.intent_id))
    {
        insert_indexed_value(
            &mut values,
            ALGORITHM_RUNS_DATABASE,
            run.algorithm_run_id.as_str(),
            encode_algorithm_run_current(run)?,
        )?;
    }
    for commitment in snapshot
        .commitments
        .iter()
        .filter(|value| value.status.consumes_capacity())
    {
        insert_indexed_value(
            &mut values,
            COMMITMENTS_DATABASE,
            commitment.order_id.as_str(),
            encode_commitment_current(commitment)?,
        )?;
    }
    for reservation in snapshot
        .risk_reservations
        .iter()
        .filter(|value| active_risk_reservation_status(value.status))
    {
        insert_indexed_value(
            &mut values,
            RISK_RESERVATIONS_DATABASE,
            reservation.reservation_id.as_str(),
            encode_risk_reservation_current(reservation)?,
        )?;
    }
    for remote in snapshot.unknown_remote_orders.iter().filter(|value| {
        matches!(
            value.resolution,
            crate::domain::UnknownRemoteOrderResolution::Pending
                | crate::domain::UnknownRemoteOrderResolution::ManualReview
        )
    }) {
        insert_indexed_value(
            &mut values,
            UNKNOWN_REMOTE_ORDERS_DATABASE,
            &remote.remote_order_id,
            encode_unknown_remote_order_current(remote),
        )?;
    }
    return Ok(values);

    fn insert_indexed_value(
        values: &mut std::collections::BTreeMap<(String, Vec<u8>), Vec<u8>>,
        database: &str,
        identity: &str,
        bytes: Vec<u8>,
    ) -> Result<(), String> {
        let key = indexed_entity_key(identity).map_err(|error| error.to_string())?;
        if values.insert((database.to_owned(), key), bytes).is_some() {
            return Err(format!(
                "duplicate Execution indexed current value: database={database}, identity={identity}"
            ));
        }
        Ok(())
    }
}

fn encode_order_current(order: &ExecutionOrder) -> Result<Vec<u8>, String> {
    let mut builder = FlatBufferBuilder::new();
    let state = encode_order_state(&mut builder, order)?;
    let root = fb::ExecutionOrderCurrent::create(
        &mut builder,
        &fb::ExecutionOrderCurrentArgs { state: Some(state) },
    );
    fb::finish_execution_order_current_buffer(&mut builder, root);
    Ok(builder.finished_data().to_vec())
}

fn encode_intent_current(intent: &crate::domain::IntentState) -> Result<Vec<u8>, String> {
    let mut builder = FlatBufferBuilder::new();
    let state = encode_intent_state(&mut builder, intent)?;
    let root = fb::ExecutionIntentCurrent::create(
        &mut builder,
        &fb::ExecutionIntentCurrentArgs { state: Some(state) },
    );
    fb::finish_execution_intent_current_buffer(&mut builder, root);
    Ok(builder.finished_data().to_vec())
}

fn encode_algorithm_run_current(run: &crate::domain::AlgorithmRun) -> Result<Vec<u8>, String> {
    let mut builder = FlatBufferBuilder::new();
    let state = encode_algorithm_run_state(&mut builder, run)?;
    let root = fb::ExecutionAlgorithmRunCurrent::create(
        &mut builder,
        &fb::ExecutionAlgorithmRunCurrentArgs { state: Some(state) },
    );
    fb::finish_execution_algorithm_run_current_buffer(&mut builder, root);
    Ok(builder.finished_data().to_vec())
}

fn encode_commitment_current(commitment: &OrderCommitment) -> Result<Vec<u8>, String> {
    let mut builder = FlatBufferBuilder::new();
    let state = encode_commitment_state(&mut builder, commitment)?;
    let root = fb::ExecutionCommitmentCurrent::create(
        &mut builder,
        &fb::ExecutionCommitmentCurrentArgs { state: Some(state) },
    );
    fb::finish_execution_commitment_current_buffer(&mut builder, root);
    Ok(builder.finished_data().to_vec())
}

fn encode_risk_reservation_current(
    reservation: &RiskReservationEvidence,
) -> Result<Vec<u8>, String> {
    let mut builder = FlatBufferBuilder::new();
    let state = encode_risk_reservation_state(&mut builder, reservation)?;
    let root = fb::ExecutionRiskReservationCurrent::create(
        &mut builder,
        &fb::ExecutionRiskReservationCurrentArgs { state: Some(state) },
    );
    fb::finish_execution_risk_reservation_current_buffer(&mut builder, root);
    Ok(builder.finished_data().to_vec())
}

fn encode_unknown_remote_order_current(remote: &crate::domain::UnknownRemoteOrder) -> Vec<u8> {
    let mut builder = FlatBufferBuilder::new();
    let state = encode_unknown_remote_order(&mut builder, remote);
    let root = fb::ExecutionUnknownRemoteOrderCurrent::create(
        &mut builder,
        &fb::ExecutionUnknownRemoteOrderCurrentArgs { state: Some(state) },
    );
    fb::finish_execution_unknown_remote_order_current_buffer(&mut builder, root);
    builder.finished_data().to_vec()
}

pub(super) fn encode_unknown_remote_order<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    order: &crate::domain::UnknownRemoteOrder,
) -> flatbuffers::WIPOffset<fb::UnknownRemoteOrderState<'a>> {
    let remote_order_id = builder.create_string(order.remote_order_id.as_str());
    let symbol = builder.create_string(order.symbol.as_str());
    let execution_id = order
        .execution_id
        .as_ref()
        .map(|value| builder.create_string(value.as_str()));
    let fill_quantity = order.fill_quantity.map(decimal);
    let fill_price = order.fill_price.map(decimal);
    let fee_currency = order
        .fee_currency
        .as_ref()
        .map(|value| builder.create_string(value.as_str()));
    let fee_amount = order.fee_amount.map(decimal);
    let resolution = builder.create_string(match order.resolution {
        crate::domain::UnknownRemoteOrderResolution::Pending => "pending",
        crate::domain::UnknownRemoteOrderResolution::LinkedToLocalOrder => "linked_to_local_order",
        crate::domain::UnknownRemoteOrderResolution::ImportedAsExternalOrder => {
            "imported_as_external_order"
        },
        crate::domain::UnknownRemoteOrderResolution::ManualReview => "manual_review",
    });
    let reason = (!order.reason.is_empty()).then(|| builder.create_string(&order.reason));
    fb::UnknownRemoteOrderState::create(
        builder,
        &fb::UnknownRemoteOrderStateArgs {
            remote_order_id: Some(remote_order_id),
            symbol: Some(symbol),
            lifecycle: order_lifecycle(order.status),
            execution_id,
            fill_quantity: fill_quantity.as_ref(),
            fill_price: fill_price.as_ref(),
            fee_currency,
            fee_amount: fee_amount.as_ref(),
            first_seen_at_unix_nanos: order.first_seen_at_unix_nanos.get(),
            last_seen_at_unix_nanos: order.last_seen_at_unix_nanos.get(),
            resolution: Some(resolution),
            reason,
        },
    )
}

pub(super) fn encode_commitment_state<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    commitment: &OrderCommitment,
) -> Result<flatbuffers::WIPOffset<fb::OrderCommitmentState<'a>>, String> {
    let order_id = builder.create_string(commitment.order_id.as_str());
    let account_id = builder.create_string(commitment.account_id.as_str());
    let segment_key = builder.create_string(commitment.segment_key.as_str());
    let instrument_id = builder.create_string(commitment.instrument_id.as_str());
    let (resource_kind, resource_id, position_side) = match &commitment.resource {
        CommitmentResource::Asset(value) => (
            fb::CommitmentResourceKind::ASSET,
            value.as_str(),
            fb::CommitmentPositionSide::UNSPECIFIED,
        ),
        CommitmentResource::Instrument(value) => (
            fb::CommitmentResourceKind::INSTRUMENT,
            value.as_str(),
            fb::CommitmentPositionSide::UNSPECIFIED,
        ),
        CommitmentResource::CloseablePosition {
            instrument_id,
            position_side,
        } => {
            let position_side = match position_side {
                kairos_primitives::account::PositionSide::Net => fb::CommitmentPositionSide::NET,
                kairos_primitives::account::PositionSide::Long => fb::CommitmentPositionSide::LONG,
                kairos_primitives::account::PositionSide::Short => {
                    fb::CommitmentPositionSide::SHORT
                },
            };
            (
                fb::CommitmentResourceKind::CLOSEABLE_POSITION,
                instrument_id.as_str(),
                position_side,
            )
        },
        CommitmentResource::MarginNotional(value) => (
            fb::CommitmentResourceKind::MARGIN_NOTIONAL,
            value.as_str(),
            fb::CommitmentPositionSide::UNSPECIFIED,
        ),
    };
    let resource_id = builder.create_string(resource_id);
    let settlement_asset = commitment
        .settlement_asset
        .as_ref()
        .map(|value| builder.create_string(value.as_str()));
    let amount = decimal(commitment.amount);
    let remaining_quantity = decimal(commitment.remaining_quantity);
    let (basis_kind, price_cap, contract_size) = match commitment.basis {
        CommitmentBasis::QuotePriceCap { price_cap } => (
            fb::CommitmentBasisKind::QUOTE_PRICE_CAP,
            Some(decimal(price_cap)),
            None,
        ),
        CommitmentBasis::BaseQuantity => (fb::CommitmentBasisKind::BASE_QUANTITY, None, None),
        CommitmentBasis::CloseablePositionQuantity => (
            fb::CommitmentBasisKind::CLOSEABLE_POSITION_QUANTITY,
            None,
            None,
        ),
        CommitmentBasis::ContractNotional {
            price_cap,
            contract_size,
        } => (
            fb::CommitmentBasisKind::CONTRACT_NOTIONAL,
            Some(decimal(price_cap)),
            Some(decimal(contract_size)),
        ),
        CommitmentBasis::SimulationQuantity => {
            (fb::CommitmentBasisKind::SIMULATION_QUANTITY, None, None)
        },
    };
    Ok(fb::OrderCommitmentState::create(
        builder,
        &fb::OrderCommitmentStateArgs {
            order_id: Some(order_id),
            account_id: Some(account_id),
            segment_key: Some(segment_key),
            instrument_id: Some(instrument_id),
            side: match commitment.side {
                OrderSide::Buy => kairos_protocol::generated::kairos::common::v_2::Side::BUY,
                OrderSide::Sell => kairos_protocol::generated::kairos::common::v_2::Side::SELL,
            },
            resource_kind,
            resource_id: Some(resource_id),
            position_side,
            amount: Some(&amount),
            remaining_quantity: Some(&remaining_quantity),
            lifecycle: match commitment.status {
                CommitmentStatus::HeldBeforeSend => fb::CommitmentLifecycle::HELD_BEFORE_SEND,
                CommitmentStatus::Active => fb::CommitmentLifecycle::ACTIVE,
                CommitmentStatus::Uncertain => fb::CommitmentLifecycle::UNCERTAIN,
                CommitmentStatus::Reduced => fb::CommitmentLifecycle::REDUCED,
                CommitmentStatus::Released => fb::CommitmentLifecycle::RELEASED,
                CommitmentStatus::Reconciled => fb::CommitmentLifecycle::RECONCILED,
            },
            basis_kind,
            price_cap: price_cap.as_ref(),
            contract_size: contract_size.as_ref(),
            settlement_asset,
            reflected_account_watermark: commitment
                .reflected_account_watermark
                .map(|value| value.get()),
            updated_at_unix_nanos: commitment.updated_at_unix_nanos.get(),
        },
    ))
}

pub(super) fn encode_risk_reservation_state<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    reservation: &RiskReservationEvidence,
) -> Result<flatbuffers::WIPOffset<fb::RiskReservationSagaState<'a>>, String> {
    let order_id = builder.create_string(reservation.order_id.as_str());
    let reservation_id = builder.create_string(&reservation.reservation_id);
    let idempotency_key = builder.create_string(&reservation.idempotency_key);
    let account_id = builder.create_string(reservation.account_id.as_str());
    let amount = decimal(reservation.amount);
    let funding_requirement = reservation.funding_requirement.as_ref().map(|requirement| {
        let margin_rule_id = builder.create_string(&requirement.margin_rule_id);
        let risk_decision_id = builder.create_string(&requirement.risk_decision_id);
        let broker = builder.create_string(&requirement.broker);
        let segment = builder.create_string(&requirement.segment);
        let collateral_asset = builder.create_string(&requirement.collateral_asset);
        let required_margin = decimal(requirement.required_margin);
        let available_margin = decimal(requirement.available_margin);
        let shortfall = decimal(requirement.shortfall);
        fb::ExecutionFundingRequirement::create(
            builder,
            &fb::ExecutionFundingRequirementArgs {
                required_margin: Some(&required_margin),
                available_margin: Some(&available_margin),
                shortfall: Some(&shortfall),
                margin_rule_id: Some(margin_rule_id),
                risk_decision_id: Some(risk_decision_id),
                risk_policy_version: requirement.risk_policy_version.get(),
                account_snapshot_watermark: requirement.account_snapshot_watermark.get(),
                broker: Some(broker),
                segment: Some(segment),
                collateral_asset: Some(collateral_asset),
            },
        )
    });
    Ok(fb::RiskReservationSagaState::create(
        builder,
        &fb::RiskReservationSagaStateArgs {
            order_id: Some(order_id),
            reservation_id: Some(reservation_id),
            idempotency_key: Some(idempotency_key),
            account_id: Some(account_id),
            amount: Some(&amount),
            lifecycle: match reservation.status {
                RiskReservationSagaStatus::AuthorizePending => {
                    fb::RiskReservationSagaLifecycle::AUTHORIZE_PENDING
                },
                RiskReservationSagaStatus::Active => fb::RiskReservationSagaLifecycle::ACTIVE,
                RiskReservationSagaStatus::ResizePending => {
                    fb::RiskReservationSagaLifecycle::RESIZE_PENDING
                },
                RiskReservationSagaStatus::ReleasePending => {
                    fb::RiskReservationSagaLifecycle::RELEASE_PENDING
                },
                RiskReservationSagaStatus::ConsumePending => {
                    fb::RiskReservationSagaLifecycle::CONSUME_PENDING
                },
                RiskReservationSagaStatus::Released => fb::RiskReservationSagaLifecycle::RELEASED,
                RiskReservationSagaStatus::Consumed => fb::RiskReservationSagaLifecycle::CONSUMED,
                RiskReservationSagaStatus::Expired => fb::RiskReservationSagaLifecycle::EXPIRED,
                RiskReservationSagaStatus::Failed => fb::RiskReservationSagaLifecycle::FAILED,
                RiskReservationSagaStatus::Uncertain => fb::RiskReservationSagaLifecycle::UNCERTAIN,
            },
            risk_generation: reservation.risk_generation.get(),
            risk_event_sequence: reservation.risk_event_sequence.get(),
            policy_version: reservation.policy_version.get(),
            expires_at_unix_nanos: reservation.expires_at_unix_nanos.get(),
            updated_at_unix_nanos: reservation.updated_at_unix_nanos.get(),
            funding_requirement,
        },
    ))
}

fn encode_selected_route<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    route: &crate::domain::SelectedExecutionRoute,
) -> flatbuffers::WIPOffset<fb::SelectedExecutionRoute<'a>> {
    let route_id = builder.create_string(route.route_id.as_str());
    let broker_id = builder.create_string(&route.broker_id);
    let destination_market_id = route
        .destination_market_id
        .as_ref()
        .map(|value| builder.create_string(value.as_str()));
    let execution_channel = builder.create_string(route.execution_channel.as_str());
    let order_entry_symbol = builder.create_string(route.order_entry_symbol.as_str());
    fb::SelectedExecutionRoute::create(
        builder,
        &fb::SelectedExecutionRouteArgs {
            route_id: Some(route_id),
            selection_kind: match route.selection_kind {
                crate::domain::RouteSelectionKind::Explicit => fb::RouteSelectionKind::EXPLICIT,
                crate::domain::RouteSelectionKind::UniqueCandidate => {
                    fb::RouteSelectionKind::UNIQUE_CANDIDATE
                },
            },
            broker_id: Some(broker_id),
            destination_market_id,
            execution_channel: Some(execution_channel),
            provider_symbol: Some(order_entry_symbol),
            selected_at_unix_nanos: route.selected_at_unix_nanos.get(),
        },
    )
}

fn encode_attempt<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    attempt: &crate::domain::ExecutionAttempt,
) -> flatbuffers::WIPOffset<fb::ExecutionAttempt<'a>> {
    let attempt_id = builder.create_string(&attempt.attempt_id);
    let selected_route = encode_selected_route(builder, &attempt.selected_route);
    let provider_connection_id = builder.create_string(&attempt.provider_connection_id);
    let remote_order_id = attempt
        .remote_order_id
        .as_ref()
        .map(|value| builder.create_string(value.as_str()));
    fb::ExecutionAttempt::create(
        builder,
        &fb::ExecutionAttemptArgs {
            attempt_id: Some(attempt_id),
            command: match attempt.command {
                crate::domain::ExecutionCommandKind::Submit => fb::ExecutionCommandKind::SUBMIT,
                crate::domain::ExecutionCommandKind::Cancel => fb::ExecutionCommandKind::CANCEL,
            },
            selected_route: Some(selected_route),
            provider_connection_id: Some(provider_connection_id),
            command_started_at_unix_nanos: attempt.command_started_at_unix_nanos.get(),
            delivery_certainty: match attempt.delivery_certainty {
                crate::domain::DeliveryCertainty::NotSent => fb::DeliveryCertainty::NOT_SENT,
                crate::domain::DeliveryCertainty::Indeterminate => {
                    fb::DeliveryCertainty::INDETERMINATE
                },
                crate::domain::DeliveryCertainty::Confirmed => fb::DeliveryCertainty::CONFIRMED,
                crate::domain::DeliveryCertainty::Rejected => fb::DeliveryCertainty::REJECTED,
                crate::domain::DeliveryCertainty::Reconciled => fb::DeliveryCertainty::RECONCILED,
            },
            remote_order_id,
        },
    )
}

pub(super) fn encode_order_state<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    order: &ExecutionOrder,
) -> Result<flatbuffers::WIPOffset<fb::OrderState<'a>>, String> {
    let order_id = builder.create_string(&order.order_id.to_string());
    let intent_id = builder.create_string(
        &order
            .intent_id
            .as_ref()
            .map(ToString::to_string)
            .unwrap_or_default(),
    );
    let plan_id = builder.create_string(
        &order
            .plan_id
            .as_ref()
            .map(ToString::to_string)
            .unwrap_or_default(),
    );
    let leg_id = builder.create_string(
        &order
            .leg_id
            .as_ref()
            .map(ToString::to_string)
            .unwrap_or_default(),
    );
    let strategy_id = builder.create_string(
        &order
            .strategy_id
            .as_ref()
            .map(ToString::to_string)
            .unwrap_or_default(),
    );
    let account_id = builder.create_string(&order.account_id.to_string());
    let segment_key = builder.create_string(&order.segment_key.to_string());
    let instrument_id = builder.create_string(&order.instrument_id.to_string());
    let market_id = builder.create_string(
        &order
            .market_id
            .as_ref()
            .map(ToString::to_string)
            .unwrap_or_default(),
    );
    let execution_route_id = builder.create_string(
        &order
            .execution_route_id
            .as_ref()
            .map(ToString::to_string)
            .unwrap_or_default(),
    );
    let remote_order_id = order
        .remote_order_id
        .as_ref()
        .map(|value| builder.create_string(&value.to_string()));
    let selected_route = order
        .selected_route
        .as_ref()
        .map(|route| encode_selected_route(builder, route));
    let attempt_offsets = order
        .attempts
        .iter()
        .map(|attempt| encode_attempt(builder, attempt))
        .collect::<Vec<_>>();
    let attempts = (!attempt_offsets.is_empty()).then(|| builder.create_vector(&attempt_offsets));
    let quantity = decimal(order.quantity);
    let filled_quantity = decimal(order.filled_quantity);
    let limit_price = order.limit_price.map(decimal);
    let reason = (!order.reason.is_empty()).then(|| builder.create_string(&order.reason));
    Ok(fb::OrderState::create(
        builder,
        &fb::OrderStateArgs {
            order_id: Some(order_id),
            intent_id: Some(intent_id),
            plan_id: Some(plan_id),
            leg_id: Some(leg_id),
            strategy_id: Some(strategy_id),
            account_id: Some(account_id),
            segment_key: Some(segment_key),
            instrument_id: Some(instrument_id),
            market_id: Some(market_id),
            execution_route_id: Some(execution_route_id),
            selected_route,
            attempts,
            remote_order_id,
            side: match order.side {
                OrderSide::Buy => kairos_protocol::generated::kairos::common::v_2::Side::BUY,
                OrderSide::Sell => kairos_protocol::generated::kairos::common::v_2::Side::SELL,
            },
            order_type: match order.order_type {
                OrderType::Market => fb::OrderType::MARKET,
                OrderType::Limit => fb::OrderType::LIMIT,
            },
            quantity: Some(&quantity),
            filled_quantity: Some(&filled_quantity),
            limit_price: limit_price.as_ref(),
            average_fill_price: None,
            lifecycle: order_lifecycle(order.status),
            created_at_unix_nanos: order.submitted_at_unix_nanos.get(),
            submitted_at_unix_nanos: Some(order.submitted_at_unix_nanos.get()),
            updated_at_unix_nanos: order.updated_at_unix_nanos.get(),
            reason,
        },
    ))
}

pub(super) fn order_lifecycle(status: ExecutionOrderStatus) -> fb::OrderLifecycle {
    match status {
        ExecutionOrderStatus::Pending => fb::OrderLifecycle::PENDING,
        ExecutionOrderStatus::Submitting => fb::OrderLifecycle::SUBMITTING,
        ExecutionOrderStatus::Accepted => fb::OrderLifecycle::ACCEPTED,
        ExecutionOrderStatus::PartiallyFilled => fb::OrderLifecycle::PARTIALLY_FILLED,
        ExecutionOrderStatus::Filled => fb::OrderLifecycle::FILLED,
        ExecutionOrderStatus::CancelRequested => fb::OrderLifecycle::CANCEL_REQUESTED,
        ExecutionOrderStatus::Canceled => fb::OrderLifecycle::CANCELED,
        ExecutionOrderStatus::Rejected => fb::OrderLifecycle::REJECTED,
        ExecutionOrderStatus::Expired => fb::OrderLifecycle::EXPIRED,
        ExecutionOrderStatus::Unknown => fb::OrderLifecycle::UNKNOWN,
        ExecutionOrderStatus::Failed => fb::OrderLifecycle::FAILED,
    }
}

pub(super) fn intent_lifecycle(status: crate::domain::IntentStatus) -> fb::IntentLifecycle {
    use crate::domain::IntentStatus;
    match status {
        IntentStatus::Accepted => fb::IntentLifecycle::ACCEPTED,
        IntentStatus::Planning => fb::IntentLifecycle::PLANNING,
        IntentStatus::Planned => fb::IntentLifecycle::PLANNED,
        IntentStatus::Executing => fb::IntentLifecycle::EXECUTING,
        IntentStatus::PartiallyFilled => fb::IntentLifecycle::PARTIALLY_FILLED,
        IntentStatus::CancelRequested => fb::IntentLifecycle::CANCEL_REQUESTED,
        IntentStatus::Satisfied => fb::IntentLifecycle::SATISFIED,
        IntentStatus::Rejected => fb::IntentLifecycle::REJECTED,
        IntentStatus::Canceled => fb::IntentLifecycle::CANCELED,
        IntentStatus::Expired => fb::IntentLifecycle::EXPIRED,
        IntentStatus::Failed => fb::IntentLifecycle::FAILED,
        IntentStatus::Compensating => fb::IntentLifecycle::COMPENSATING,
        IntentStatus::ReconciliationRequired => fb::IntentLifecycle::RECONCILIATION_REQUIRED,
    }
}

pub(super) fn decimal(
    value: impl Into<kairos_primitives::decimal::DecimalParts>,
) -> kairos_protocol::generated::kairos::common::v_2::Decimal64 {
    let value = value.into();
    kairos_protocol::generated::kairos::common::v_2::Decimal64::new(value.mantissa(), value.scale())
}

pub(super) fn encode_algorithm_run_state<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    run: &crate::domain::AlgorithmRun,
) -> Result<flatbuffers::WIPOffset<fb::AlgorithmRunState<'a>>, String> {
    let mut leg_offsets = Vec::with_capacity(run.legs.len());
    for leg in &run.legs {
        let quality = run
            .quality
            .legs
            .iter()
            .find(|quality| quality.leg_id == leg.leg_id);
        let mut fee_offsets = Vec::new();
        if let Some(quality) = quality {
            for fee in &quality.fee_totals {
                let currency = builder.create_string(fee.currency.as_str());
                let amount = decimal(fee.amount);
                fee_offsets.push(fb::ExecutionFeeTotal::create(
                    builder,
                    &fb::ExecutionFeeTotalArgs {
                        currency: Some(currency),
                        amount: Some(&amount),
                    },
                ));
            }
        }
        let fee_totals = builder.create_vector(&fee_offsets);
        let quality_filled = decimal(
            quality
                .map(|quality| quality.filled_quantity)
                .unwrap_or(Quantity::ZERO),
        );
        let gross_notional = decimal(
            quality
                .map(|quality| quality.gross_notional)
                .unwrap_or(Money::ZERO),
        );
        let average_fill_price = quality
            .and_then(|quality| quality.average_fill_price)
            .map(decimal);
        let benchmark_offset = leg.benchmark.as_ref().map(|benchmark| {
            let derived = quality.and_then(|quality| quality.benchmark.as_ref());
            let instrument_id = builder.create_string(benchmark.instrument_id.as_str());
            let market_id = builder.create_string(benchmark.market_id.as_str());
            let price = decimal(benchmark.price);
            let benchmark_notional = decimal(
                derived
                    .map(|benchmark| benchmark.benchmark_notional)
                    .unwrap_or(Money::ZERO),
            );
            let implementation_shortfall = derived
                .and_then(|benchmark| benchmark.implementation_shortfall)
                .map(decimal);
            fb::AlgorithmLegBenchmarkQuality::create(
                builder,
                &fb::AlgorithmLegBenchmarkQualityArgs {
                    kind: match benchmark.kind {
                        crate::domain::ExecutionBenchmarkKind::Arrival => {
                            fb::ExecutionBenchmarkKind::ARRIVAL
                        },
                    },
                    instrument_id: Some(instrument_id),
                    market_id: Some(market_id),
                    price: Some(&price),
                    observed_at_unix_nanos: benchmark.observed_at_unix_nanos.get(),
                    benchmark_notional: Some(&benchmark_notional),
                    implementation_shortfall: implementation_shortfall.as_ref(),
                },
            )
        });
        let quality_offset = fb::AlgorithmLegExecutionQuality::create(
            builder,
            &fb::AlgorithmLegExecutionQualityArgs {
                order_count: quality.map(|quality| quality.order_count).unwrap_or(0),
                fill_count: quality.map(|quality| quality.fill_count).unwrap_or(0),
                cancel_attempt_count: quality
                    .map(|quality| quality.cancel_attempt_count)
                    .unwrap_or(0),
                filled_quantity: Some(&quality_filled),
                gross_notional: Some(&gross_notional),
                average_fill_price: average_fill_price.as_ref(),
                first_order_submitted_at_unix_nanos: quality
                    .and_then(|quality| quality.first_order_submitted_at)
                    .map(UnixNanos::get),
                first_fill_at_unix_nanos: quality
                    .and_then(|quality| quality.first_fill_at)
                    .map(UnixNanos::get),
                last_fill_at_unix_nanos: quality
                    .and_then(|quality| quality.last_fill_at)
                    .map(UnixNanos::get),
                time_to_first_fill_nanos: quality
                    .and_then(|quality| quality.time_to_first_fill)
                    .map(kairos_primitives::time::DurationNanos::get),
                time_to_last_fill_nanos: quality
                    .and_then(|quality| quality.time_to_last_fill)
                    .map(kairos_primitives::time::DurationNanos::get),
                fee_totals: Some(fee_totals),
                benchmark: benchmark_offset,
            },
        );
        let leg_id = builder.create_string(leg.leg_id.as_str());
        let target_quantity = decimal(leg.target_quantity);
        let committed_quantity = decimal(leg.committed_quantity);
        let filled_quantity = decimal(leg.filled_quantity);
        leg_offsets.push(fb::AlgorithmLegState::create(
            builder,
            &fb::AlgorithmLegStateArgs {
                leg_id: Some(leg_id),
                role: match leg.role {
                    crate::domain::AlgorithmLegRole::Immediate => fb::AlgorithmLegRole::IMMEDIATE,
                    crate::domain::AlgorithmLegRole::Twap => fb::AlgorithmLegRole::TWAP,
                    crate::domain::AlgorithmLegRole::PassiveLimit => {
                        fb::AlgorithmLegRole::PASSIVE_LIMIT
                    },
                    crate::domain::AlgorithmLegRole::LeaderMaker => {
                        fb::AlgorithmLegRole::LEADER_MAKER
                    },
                    crate::domain::AlgorithmLegRole::HedgeTaker => {
                        fb::AlgorithmLegRole::HEDGE_TAKER
                    },
                    crate::domain::AlgorithmLegRole::Unwind => fb::AlgorithmLegRole::UNWIND,
                },
                lifecycle: match leg.lifecycle {
                    crate::domain::AlgorithmLegLifecycle::Dormant => {
                        fb::AlgorithmLegLifecycle::DORMANT
                    },
                    crate::domain::AlgorithmLegLifecycle::Ready => fb::AlgorithmLegLifecycle::READY,
                    crate::domain::AlgorithmLegLifecycle::Active => {
                        fb::AlgorithmLegLifecycle::ACTIVE
                    },
                    crate::domain::AlgorithmLegLifecycle::Completed => {
                        fb::AlgorithmLegLifecycle::COMPLETED
                    },
                    crate::domain::AlgorithmLegLifecycle::Failed => {
                        fb::AlgorithmLegLifecycle::FAILED
                    },
                    crate::domain::AlgorithmLegLifecycle::ReconciliationRequired => {
                        fb::AlgorithmLegLifecycle::RECONCILIATION_REQUIRED
                    },
                },
                target_quantity: Some(&target_quantity),
                committed_quantity: Some(&committed_quantity),
                filled_quantity: Some(&filled_quantity),
                quality: Some(quality_offset),
            },
        ));
    }
    let legs = builder.create_vector(&leg_offsets);
    let algorithm_run_id = builder.create_string(run.algorithm_run_id.as_str());
    let intent_id = builder.create_string(run.intent_id.as_str());
    let algorithm_kind = builder.create_string(match run.spec {
        crate::domain::ExecutionAlgorithmSpec::Immediate => "immediate",
        crate::domain::ExecutionAlgorithmSpec::Twap(_) => "twap",
        crate::domain::ExecutionAlgorithmSpec::PassiveLimit(_) => "passive_limit",
        crate::domain::ExecutionAlgorithmSpec::MakerTakerHedge(_) => "maker_taker_hedge",
    });
    Ok(fb::AlgorithmRunState::create(
        builder,
        &fb::AlgorithmRunStateArgs {
            algorithm_run_id: Some(algorithm_run_id),
            algorithm_version: run.algorithm_version,
            intent_id: Some(intent_id),
            algorithm_kind: Some(algorithm_kind),
            lifecycle: match run.status {
                crate::domain::AlgorithmRunStatus::Planned => fb::AlgorithmRunLifecycle::PLANNED,
                crate::domain::AlgorithmRunStatus::Running => fb::AlgorithmRunLifecycle::RUNNING,
                crate::domain::AlgorithmRunStatus::Waiting => fb::AlgorithmRunLifecycle::WAITING,
                crate::domain::AlgorithmRunStatus::Completed => {
                    fb::AlgorithmRunLifecycle::COMPLETED
                },
                crate::domain::AlgorithmRunStatus::Unwound => fb::AlgorithmRunLifecycle::UNWOUND,
                crate::domain::AlgorithmRunStatus::Failed => fb::AlgorithmRunLifecycle::FAILED,
                crate::domain::AlgorithmRunStatus::ReconciliationRequired => {
                    fb::AlgorithmRunLifecycle::RECONCILIATION_REQUIRED
                },
            },
            decision_sequence: run.decision_sequence.get(),
            last_decision_at_unix_nanos: run.last_decision_at.map(UnixNanos::get),
            next_wake_at_unix_nanos: run.next_wake_at.map(UnixNanos::get),
            action_count: u64::try_from(run.actions.len())
                .map_err(|_| "algorithm action count overflow".to_string())?,
            pending_action_count: u64::try_from(run.pending_actions().count())
                .map_err(|_| "algorithm pending action count overflow".to_string())?,
            indeterminate_action_count: u64::try_from(
                run.actions
                    .iter()
                    .filter(|action| {
                        action.status == crate::domain::AlgorithmActionStatus::Indeterminate
                    })
                    .count(),
            )
            .map_err(|_| "algorithm indeterminate action count overflow".to_string())?,
            legs: Some(legs),
        },
    ))
}

pub(super) fn active_intent_status(status: crate::domain::IntentStatus) -> bool {
    matches!(
        status,
        crate::domain::IntentStatus::Accepted
            | crate::domain::IntentStatus::Planning
            | crate::domain::IntentStatus::Planned
            | crate::domain::IntentStatus::Executing
            | crate::domain::IntentStatus::PartiallyFilled
            | crate::domain::IntentStatus::CancelRequested
            | crate::domain::IntentStatus::Compensating
            | crate::domain::IntentStatus::ReconciliationRequired
    )
}

pub(super) fn encode_intent_state<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    state: &crate::domain::IntentState,
) -> Result<flatbuffers::WIPOffset<fb::IntentState<'a>>, String> {
    let intent_offset = encode_execution_intent(builder, &state.intent)?;
    let dependency_evidence = encode_dependency_evidence(builder, &state.dependency_watermarks);
    let state_reason = (!state.reason.is_empty()).then(|| builder.create_string(&state.reason));
    let plan = state
        .plan
        .as_ref()
        .map(|plan| encode_plan(builder, plan))
        .transpose()?;
    Ok(fb::IntentState::create(
        builder,
        &fb::IntentStateArgs {
            intent: Some(intent_offset),
            lifecycle: intent_lifecycle(state.status),
            plan,
            updated_at_unix_nanos: state.updated_at_unix_nanos.get(),
            reason: state_reason,
            dependency_evidence: Some(dependency_evidence),
            quote_version: Some(state.quote_version),
            last_quote_refresh_unix_nanos: state.last_quote_refresh_unix_nanos.map(|v| v.get()),
            compensation_attempts: state.compensation_attempts,
        },
    ))
}

pub(super) fn encode_execution_intent<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    intent: &crate::domain::ExecuteStrategyIntent,
) -> Result<flatbuffers::WIPOffset<fb::ExecutionIntent<'a>>, String> {
    let intent_id = builder.create_string(&intent.intent_id.to_string());
    let strategy_decision_id = intent
        .strategy_decision_id
        .as_ref()
        .map(|value| builder.create_string(value));
    let strategy_id = builder.create_string(&intent.strategy_id);
    let launch_id = builder.create_string(&intent.launch_id);
    let instance_id = builder.create_string(&intent.instance_id);
    let reason = (!intent.reason.is_empty()).then(|| builder.create_string(&intent.reason));
    let leg_offsets = intent
        .legs
        .iter()
        .map(|leg| encode_intent_leg(builder, leg))
        .collect::<Result<Vec<_>, _>>()?;
    let legs = builder.create_vector(&leg_offsets);
    let benchmark_offsets = intent
        .execution_benchmarks
        .iter()
        .map(|benchmark| {
            let leg_id = benchmark
                .leg_id
                .as_ref()
                .map(|leg_id| builder.create_string(leg_id.as_str()));
            let instrument_id = builder.create_string(benchmark.instrument_id.as_str());
            let market_id = builder.create_string(benchmark.market_id.as_str());
            let price = decimal(benchmark.price);
            fb::ExecutionBenchmarkObservation::create(
                builder,
                &fb::ExecutionBenchmarkObservationArgs {
                    kind: match benchmark.kind {
                        crate::domain::ExecutionBenchmarkKind::Arrival => {
                            fb::ExecutionBenchmarkKind::ARRIVAL
                        },
                    },
                    leg_id,
                    instrument_id: Some(instrument_id),
                    market_id: Some(market_id),
                    price: Some(&price),
                    observed_at_unix_nanos: benchmark.observed_at_unix_nanos.get(),
                },
            )
        })
        .collect::<Vec<_>>();
    let execution_benchmarks = builder.create_vector(&benchmark_offsets);
    let evidence = builder.create_vector::<flatbuffers::WIPOffset<
        kairos_protocol::generated::kairos::common::v_2::EvidenceRef,
    >>(&[]);
    let (algorithm_type, algorithm) = match &intent.algorithm {
        crate::domain::ExecutionAlgorithmPolicy::Immediate => {
            let value =
                fb::ImmediateAlgorithm::create(builder, &fb::ImmediateAlgorithmArgs::default());
            (
                fb::ExecutionAlgorithm::ImmediateAlgorithm,
                value.as_union_value(),
            )
        },
        crate::domain::ExecutionAlgorithmPolicy::Twap(policy) => {
            let value = fb::TwapPolicy::create(
                builder,
                &fb::TwapPolicyArgs {
                    slice_count: policy.slice_count,
                    slice_interval_nanos: policy.slice_interval.get(),
                },
            );
            (fb::ExecutionAlgorithm::TwapPolicy, value.as_union_value())
        },
        crate::domain::ExecutionAlgorithmPolicy::PassiveLimit(policy) => {
            let value = fb::PassiveLimitPolicy::create(
                builder,
                &fb::PassiveLimitPolicyArgs {
                    reprice_interval_nanos: policy.reprice_interval.get(),
                    max_quote_age_nanos: policy.max_quote_age.get(),
                },
            );
            (
                fb::ExecutionAlgorithm::PassiveLimitPolicy,
                value.as_union_value(),
            )
        },
        crate::domain::ExecutionAlgorithmPolicy::MakerTakerHedge(policy) => {
            let leader_leg_id = builder.create_string(policy.leader_leg_id.as_str());
            let hedge_leg_id = builder.create_string(policy.hedge_leg_id.as_str());
            let fallback_routes = policy
                .fallback_execution_route_ids
                .iter()
                .map(|route_id| builder.create_string(route_id.as_str()))
                .collect::<Vec<_>>();
            let fallback_execution_route_ids = builder.create_vector(&fallback_routes);
            let ratio = fb::Ratio::new(policy.ratio.numerator(), policy.ratio.denominator());
            let contract_multiplier = fb::Ratio::new(
                policy.contract_multiplier.numerator(),
                policy.contract_multiplier.denominator(),
            );
            let max_unhedged_quantity = decimal(policy.max_unhedged_quantity);
            let value = fb::HedgePolicy::create(
                builder,
                &fb::HedgePolicyArgs {
                    leader_leg_id: Some(leader_leg_id),
                    hedge_leg_id: Some(hedge_leg_id),
                    ratio: Some(&ratio),
                    contract_multiplier: Some(&contract_multiplier),
                    max_unhedged_quantity: Some(&max_unhedged_quantity),
                    max_unhedged_duration_nanos: policy
                        .max_unhedged_duration
                        .map(|duration| duration.get()),
                    fallback_execution_route_ids: Some(fallback_execution_route_ids),
                    compensate_on_failure: policy.compensate_on_failure,
                    max_compensation_attempts: policy.max_compensation_attempts,
                },
            );
            (fb::ExecutionAlgorithm::HedgePolicy, value.as_union_value())
        },
    };
    Ok(fb::ExecutionIntent::create(
        builder,
        &fb::ExecutionIntentArgs {
            intent_id: Some(intent_id),
            strategy_id: Some(strategy_id),
            launch_id: Some(launch_id),
            instance_id: Some(instance_id),
            intent_type: intent_type(intent.intent_type),
            algorithm_type,
            algorithm: Some(algorithm),
            legs: Some(legs),
            completion_policy: completion_policy(intent.completion_policy),
            failure_policy: failure_policy(intent.failure_policy),
            deadline_unix_nanos: intent.deadline_unix_nanos.map(|value| value.get()),
            min_edge_bps: intent.min_edge_bps,
            max_slippage_bps: intent.max_slippage_bps,
            estimated_fee_bps: intent.estimated_fee_bps,
            evidence: Some(evidence),
            reason,
            strategy_decision_id,
            execution_benchmarks: Some(execution_benchmarks),
        },
    ))
}

pub(super) fn encode_intent_leg<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    leg: &crate::domain::IntentLegRequest,
) -> Result<flatbuffers::WIPOffset<fb::IntentLeg<'a>>, String> {
    let leg_id = builder.create_string(&leg.leg_id.to_string());
    let account_id = builder.create_string(&leg.account_id.to_string());
    let segment_key = builder.create_string(&leg.segment_key.to_string());
    let instrument_id = builder.create_string(&leg.instrument_id.to_string());
    let market_id = builder.create_string(
        &leg.market_id
            .as_ref()
            .map(ToString::to_string)
            .unwrap_or_default(),
    );
    let execution_route_id = builder.create_string(
        &leg.execution_route_id
            .as_ref()
            .ok_or_else(|| "execution intent leg execution_route_id is required".to_owned())?
            .to_string(),
    );
    let quantity = decimal(leg.quantity);
    let limit_price = leg.limit_price.map(decimal);
    let options =
        fb::ExecutionOrderOptions::create(builder, &fb::ExecutionOrderOptionsArgs::default());
    Ok(fb::IntentLeg::create(
        builder,
        &fb::IntentLegArgs {
            leg_id: Some(leg_id),
            account_id: Some(account_id),
            segment_key: Some(segment_key),
            instrument_id: Some(instrument_id),
            market_id: Some(market_id),
            execution_route_id: Some(execution_route_id),
            side: match leg.side {
                OrderSide::Buy => kairos_protocol::generated::kairos::common::v_2::Side::BUY,
                OrderSide::Sell => kairos_protocol::generated::kairos::common::v_2::Side::SELL,
            },
            quantity: Some(&quantity),
            quantity_semantics: if leg.target_position {
                fb::QuantitySemantics::TARGET_POSITION
            } else {
                fb::QuantitySemantics::ORDER_QUANTITY
            },
            limit_price: limit_price.as_ref(),
            options: Some(options),
        },
    ))
}

pub(super) fn encode_plan<'a>(
    builder: &mut FlatBufferBuilder<'a>,
    plan: &crate::domain::ExecutionPlan,
) -> Result<flatbuffers::WIPOffset<fb::ExecutionPlan<'a>>, String> {
    let plan_id = builder.create_string(&plan.plan_id.to_string());
    let intent_id = builder.create_string(&plan.intent_id.to_string());
    let leg_offsets = plan
        .legs
        .iter()
        .map(|leg| {
            let leg_id = builder.create_string(&leg.leg_id.to_string());
            let account_id = builder.create_string(&leg.account_id.to_string());
            let segment_key = builder.create_string(&leg.segment_key.to_string());
            let instrument_id = builder.create_string(&leg.instrument_id.to_string());
            let market_id = builder.create_string(
                &leg.market_id
                    .as_ref()
                    .map(ToString::to_string)
                    .unwrap_or_default(),
            );
            let order_ids = leg
                .order_ids
                .iter()
                .map(|id| builder.create_string(&id.to_string()))
                .collect::<Vec<_>>();
            let order_ids = builder.create_vector(&order_ids);
            let target_quantity = decimal(leg.target_quantity);
            let completed_quantity = decimal(leg.completed_quantity);
            let reason = (!leg.reason.is_empty()).then(|| builder.create_string(&leg.reason));
            Ok(fb::ExecutionLegState::create(
                builder,
                &fb::ExecutionLegStateArgs {
                    leg_id: Some(leg_id),
                    account_id: Some(account_id),
                    segment_key: Some(segment_key),
                    instrument_id: Some(instrument_id),
                    market_id: Some(market_id),
                    side: match leg.side {
                        OrderSide::Buy => {
                            kairos_protocol::generated::kairos::common::v_2::Side::BUY
                        },
                        OrderSide::Sell => {
                            kairos_protocol::generated::kairos::common::v_2::Side::SELL
                        },
                    },
                    target_quantity: Some(&target_quantity),
                    order_ids: Some(order_ids),
                    lifecycle: leg_lifecycle(leg.lifecycle),
                    completed_quantity: Some(&completed_quantity),
                    reason,
                },
            ))
        })
        .collect::<Result<Vec<_>, String>>()?;
    let legs = builder.create_vector(&leg_offsets);
    Ok(fb::ExecutionPlan::create(
        builder,
        &fb::ExecutionPlanArgs {
            plan_id: Some(plan_id),
            intent_id: Some(intent_id),
            intent_type: intent_type(plan.intent_type),
            legs: Some(legs),
            completion_policy: completion_policy(plan.completion_policy),
            failure_policy: failure_policy(plan.failure_policy),
            created_at_unix_nanos: 0,
        },
    ))
}

pub(super) fn leg_lifecycle(value: crate::domain::LegLifecycle) -> fb::LegLifecycle {
    match value {
        crate::domain::LegLifecycle::Pending => fb::LegLifecycle::PENDING,
        crate::domain::LegLifecycle::Ready => fb::LegLifecycle::READY,
        crate::domain::LegLifecycle::Executing => fb::LegLifecycle::EXECUTING,
        crate::domain::LegLifecycle::PartiallyFilled => fb::LegLifecycle::PARTIALLY_FILLED,
        crate::domain::LegLifecycle::Satisfied => fb::LegLifecycle::SATISFIED,
        crate::domain::LegLifecycle::Canceled => fb::LegLifecycle::CANCELED,
        crate::domain::LegLifecycle::Failed => fb::LegLifecycle::FAILED,
        crate::domain::LegLifecycle::Compensating => fb::LegLifecycle::COMPENSATING,
    }
}

pub(super) fn intent_type(value: crate::domain::IntentType) -> fb::IntentType {
    match value {
        crate::domain::IntentType::SingleOrder => fb::IntentType::SINGLE_ORDER,
        crate::domain::IntentType::TargetPosition => fb::IntentType::TARGET_POSITION,
        crate::domain::IntentType::PairArbitrage => fb::IntentType::PAIR_ARBITRAGE,
        crate::domain::IntentType::OptionSpread => fb::IntentType::OPTION_SPREAD,
        crate::domain::IntentType::PortfolioRebalance => fb::IntentType::PORTFOLIO_REBALANCE,
        crate::domain::IntentType::QuoteProvisioning => fb::IntentType::QUOTE_PROVISIONING,
        crate::domain::IntentType::Hedge => fb::IntentType::HEDGE,
    }
}

pub(super) fn completion_policy(value: crate::domain::CompletionPolicy) -> fb::CompletionPolicy {
    match value {
        crate::domain::CompletionPolicy::AllLegsSatisfied => {
            fb::CompletionPolicy::ALL_LEGS_SATISFIED
        },
        crate::domain::CompletionPolicy::AllOrNothing => fb::CompletionPolicy::ALL_OR_NOTHING,
        crate::domain::CompletionPolicy::BestEffort => fb::CompletionPolicy::BEST_EFFORT,
        crate::domain::CompletionPolicy::HedgeWithinTolerance => {
            fb::CompletionPolicy::HEDGE_WITHIN_TOLERANCE
        },
        crate::domain::CompletionPolicy::TargetQuantityReached => {
            fb::CompletionPolicy::TARGET_QUANTITY_REACHED
        },
    }
}

pub(super) fn failure_policy(value: crate::domain::FailurePolicy) -> fb::FailurePolicy {
    match value {
        crate::domain::FailurePolicy::CancelRemaining => fb::FailurePolicy::CANCEL_REMAINING,
        crate::domain::FailurePolicy::ContinueOtherLegs => fb::FailurePolicy::CONTINUE_OTHER_LEGS,
        crate::domain::FailurePolicy::Compensate => fb::FailurePolicy::COMPENSATE,
        crate::domain::FailurePolicy::PauseForManualIntervention => {
            fb::FailurePolicy::PAUSE_FOR_MANUAL_INTERVENTION
        },
        crate::domain::FailurePolicy::MarkReconciliationRequired => {
            fb::FailurePolicy::MARK_RECONCILIATION_REQUIRED
        },
    }
}
