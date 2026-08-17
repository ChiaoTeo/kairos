//! Publication orchestration used by the process facade.

use super::ExecutionProcess;
use crate::services::persistence::ExecutionOutboxEvent;
use std::time::{SystemTime, UNIX_EPOCH};
use tracing::{debug, info};

impl<E, Q, S> ExecutionProcess<E, Q, S> {
    pub(super) fn publish_snapshots(&mut self) -> Result<(), String> {
        while let Some(event) = self.application.pending_business_event().cloned() {
            if let Some(publisher) = self.event_publisher.as_mut() {
                publisher.publish(&event)?;
            }
            self.application.acknowledge_business_event();
        }
        let snapshot = self.application.current_view();
        if self.last_published_generation == Some(snapshot.generation.get()) {
            return Ok(());
        }
        if let Some(publisher) = self.snapshot_publisher.as_mut() {
            publisher.publish(&snapshot)?;
        }
        if let Some(publisher) = self.intent_snapshot_publisher.as_mut() {
            publisher.publish(&snapshot)?;
        }
        debug!(
            event = "execution_snapshots_published",
            component = "execution",
            generation = snapshot.generation.get(),
            event_sequence = self.application.event_sequence(),
            order_count = snapshot.orders.len(),
            intent_count = snapshot.intents.len(),
            "execution snapshots published"
        );
        self.last_published_generation = Some(snapshot.generation.get());
        Ok(())
    }

    pub(super) fn flush_events(&mut self) -> Result<(), String> {
        if self.audit.is_none() && self.simulated_account_settlement.is_none() {
            return Ok(());
        }
        let durable_events = self
            .application
            .pending_outbox(1024)
            .map_err(|error| error.to_string())?;
        kairos_workspace::logging::record_gauge(
            "kairos.outbox.pending",
            durable_events.len() as u64,
        );
        let now_unix_nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|duration| duration.as_nanos() as u64)
            .unwrap_or_default();
        let oldest_age_ms = durable_events
            .first()
            .map(|entry| now_unix_nanos.saturating_sub(entry.created_at_unix_nanos) / 1_000_000)
            .unwrap_or_default();
        kairos_workspace::logging::record_gauge("kairos.outbox.oldest_age", oldest_age_ms);
        let checkpoint_age_ms = self
            .application
            .latest_checkpoint_unix_nanos()
            .map_err(|error| error.to_string())?
            .map(|created_at| now_unix_nanos.saturating_sub(created_at) / 1_000_000)
            .unwrap_or_default();
        kairos_workspace::logging::record_gauge("kairos.checkpoint.age", checkpoint_age_ms);
        let mut acknowledged = Vec::with_capacity(durable_events.len());
        let mut durable_orders = Vec::new();
        let mut durable_intents = Vec::new();
        for entry in &durable_events {
            match &entry.event {
                ExecutionOutboxEvent::Order(event) => {
                    if let (Some(fill_id), Some(publisher)) = (
                        event.fill_id.as_ref(),
                        self.simulated_account_settlement.as_mut(),
                    ) {
                        let (fill, order, commitment) =
                            self.application.simulated_settlement_fact(fill_id)?;
                        publisher.apply_fill(&fill, &order, &commitment)?;
                    }
                    durable_orders.push(event.clone());
                }
                ExecutionOutboxEvent::Intent(event) => durable_intents.push(event.clone()),
            }
            acknowledged.push(entry.id);
        }
        if let Some(audit) = self.audit.as_mut() {
            audit.publish_batch(&durable_orders, &durable_intents)?;
            let events = self.application.drain_events();
            let intent_events = self.application.drain_intent_events();
            audit.publish_batch(&events, &intent_events)?;
            if !events.is_empty() || !intent_events.is_empty() {
                info!(
                    event = "execution_audit_flushed",
                    component = "execution",
                    order_event_count = events.len(),
                    intent_event_count = intent_events.len(),
                    "execution audit events flushed"
                );
            }
        }
        self.application
            .acknowledge_outbox(&acknowledged)
            .map_err(|error| error.to_string())?;
        Ok(())
    }
}
