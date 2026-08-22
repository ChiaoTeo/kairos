//! Source runtime status helpers.

use std::collections::BTreeSet;

use crate::domain::{
    SourceDesiredState, SourceHealth, SourceRuntimePhase, SourceRuntimeProgress,
    SourceRuntimeWorkItem,
};

use super::SourceRuntimeRegistry;

impl SourceRuntimeRegistry {
    pub(crate) fn source_health_for_active_sources<'a>(
        &'a self,
        active_source_ids: impl IntoIterator<Item = &'a str>,
    ) -> Vec<SourceHealth> {
        let active_source_ids = active_source_ids.into_iter().collect::<BTreeSet<_>>();
        let mut source_ids = active_source_ids.iter().copied().collect::<Vec<_>>();
        source_ids.extend(self.source_ids());
        source_ids.sort_unstable();
        source_ids.dedup();
        self.health_for(source_ids)
            .into_iter()
            .map(|mut health| {
                let executable = active_source_ids.contains(health.source_id.as_str());
                let enabled = health.definition.as_ref().is_none_or(|definition| {
                    definition.desired_state == SourceDesiredState::Enabled
                });
                if !executable && enabled && health.status == SourceRuntimePhase::Idle {
                    health.status = SourceRuntimePhase::Registered;
                }
                health
            })
            .collect()
    }
}

pub(super) fn default_source_health(source_id: &str) -> SourceHealth {
    SourceHealth {
        source_id: source_id.to_owned(),
        definition: None,
        status: SourceRuntimePhase::Idle,
        progress: SourceRuntimeProgress::unknown(),
        work_item: SourceRuntimeWorkItem::default(),
        last_attempt_unix_nanos: None,
        last_success_unix_nanos: None,
        retry_after_unix_nanos: None,
        retry_backoff_seconds: None,
        consecutive_failures: 0,
        stale: false,
        last_error: None,
    }
}

pub(super) fn inactive_runtime_phase(desired_state: SourceDesiredState) -> SourceRuntimePhase {
    match desired_state {
        SourceDesiredState::Paused => SourceRuntimePhase::Paused,
        SourceDesiredState::Disabled | SourceDesiredState::Removed => SourceRuntimePhase::Disabled,
        SourceDesiredState::Enabled => SourceRuntimePhase::Idle,
    }
}
