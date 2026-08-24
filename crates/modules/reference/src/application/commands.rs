//! Contract-owned administrative commands and application mappings.

use kairos_primitives::time::Generation;
pub use kairos_reference_contract::{
    UpsertAssetRequest as UpsertAssetCommand, UpsertInstrumentRequest as UpsertInstrumentCommand,
    UpsertListingRequest as UpsertListingCommand,
};
use tracing::info;

use crate::application::ReferenceApplication;
use crate::domain::{ManualUpsertPolicy, ReferenceResult};
use crate::logging::events as log_events;

impl From<UpsertAssetCommand> for crate::domain::Asset {
    fn from(value: UpsertAssetCommand) -> Self {
        Self {
            source_id: None,
            asset_id: value.asset_id,
            code: value.code,
            name: value.name,
            asset_class: value.asset_class,
            status: value.status,
        }
    }
}

impl From<UpsertInstrumentCommand> for crate::domain::Instrument {
    fn from(value: UpsertInstrumentCommand) -> Self {
        Self {
            source_id: None,
            instrument_id: value.instrument_id,
            symbol: value.symbol,
            name: value.name,
            instrument_type: value.instrument_type,
            issuer_id: value.issuer_id,
            share_class: value.share_class,
            primary_currency_asset_id: value.primary_currency_asset_id,
            underlying_instrument_id: value.underlying_instrument_id,
            expiry_unix_nanos: value.expiry_unix_nanos,
            strike: value.strike,
            option_right: value.option_right,
            status: value.status,
        }
    }
}

impl From<UpsertListingCommand> for crate::domain::Listing {
    fn from(value: UpsertListingCommand) -> Self {
        Self {
            source_id: None,
            listing_id: value.listing_id,
            instrument_id: value.instrument_id,
            exchange_id: value.exchange_id,
            exchange_symbol: value.exchange_symbol,
            status: value.status,
            effective_from_unix_nanos: value.effective_from_unix_nanos,
            effective_to_unix_nanos: value.effective_to_unix_nanos,
        }
    }
}

impl ReferenceApplication {
    pub async fn upsert_asset(
        &mut self,
        command: UpsertAssetCommand,
    ) -> ReferenceResult<Generation> {
        let provenance = command.provenance.as_str();
        let conflict_policy = command.conflict_policy.as_str();
        let policy = manual_upsert_policy(
            provenance,
            conflict_policy,
            command.conflict_policy.rejects_provider_owned(),
        );
        let asset = crate::domain::Asset::from(command);
        let record_id = asset.asset_id.to_string();
        log_manual_upsert_started(
            "asset",
            &record_id,
            provenance,
            conflict_policy,
            "reference_asset_upsert_started",
        );
        self.actor.upsert_asset(asset, policy).await?;
        let generation = self.actor.metadata.generation;
        log_manual_upsert_completed(
            "asset",
            &record_id,
            generation,
            "reference_asset_upsert_completed",
        );
        Ok(generation)
    }

    pub async fn upsert_instrument(
        &mut self,
        command: UpsertInstrumentCommand,
    ) -> ReferenceResult<Generation> {
        let provenance = command.provenance.as_str();
        let conflict_policy = command.conflict_policy.as_str();
        let policy = manual_upsert_policy(
            provenance,
            conflict_policy,
            command.conflict_policy.rejects_provider_owned(),
        );
        let instrument = crate::domain::Instrument::from(command);
        let record_id = instrument.instrument_id.to_string();
        log_manual_upsert_started(
            "instrument",
            &record_id,
            provenance,
            conflict_policy,
            "reference_instrument_upsert_started",
        );
        self.actor.upsert_instrument(instrument, policy).await?;
        let generation = self.actor.metadata.generation;
        log_manual_upsert_completed(
            "instrument",
            &record_id,
            generation,
            "reference_instrument_upsert_completed",
        );
        Ok(generation)
    }

    pub async fn upsert_listing(
        &mut self,
        command: UpsertListingCommand,
    ) -> ReferenceResult<Generation> {
        let provenance = command.provenance.as_str();
        let conflict_policy = command.conflict_policy.as_str();
        let policy = manual_upsert_policy(
            provenance,
            conflict_policy,
            command.conflict_policy.rejects_provider_owned(),
        );
        let listing = crate::domain::Listing::from(command);
        let record_id = listing.listing_id.to_string();
        log_manual_upsert_started(
            "listing",
            &record_id,
            provenance,
            conflict_policy,
            "reference_listing_upsert_started",
        );
        self.actor.upsert_listing(listing, policy).await?;
        let generation = self.actor.metadata.generation;
        log_manual_upsert_completed(
            "listing",
            &record_id,
            generation,
            "reference_listing_upsert_completed",
        );
        Ok(generation)
    }
}

fn manual_upsert_policy(
    provenance: &str,
    conflict_policy: &str,
    reject_provider_owned: bool,
) -> ManualUpsertPolicy {
    ManualUpsertPolicy::new(provenance, conflict_policy, reject_provider_owned)
}

fn log_manual_upsert_started(
    record_kind: &'static str,
    record_id: &str,
    provenance: &str,
    conflict_policy: &str,
    legacy_event: &'static str,
) {
    let log_event = log_events::APP_COMMAND_STARTED;
    info!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event,
        record_kind,
        record_id,
        provenance,
        conflict_policy,
        "reference manual upsert started"
    );
}

fn log_manual_upsert_completed(
    record_kind: &'static str,
    record_id: &str,
    generation: Generation,
    legacy_event: &'static str,
) {
    let log_event = log_events::APP_COMMAND_COMPLETED;
    info!(
        event = log_event.event,
        component = log_event.component,
        area = log_event.area,
        action = log_event.action,
        outcome = log_event.outcome,
        legacy_event,
        record_kind,
        record_id,
        generation = generation.get(),
        "reference manual upsert completed"
    );
}
