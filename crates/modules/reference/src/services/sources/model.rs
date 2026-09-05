use crate::domain::{ProviderCatalog, SourceWorkItem};

#[derive(Clone, Debug, Default, Eq, PartialEq, serde::Serialize)]
pub(crate) struct SourceChanges {
    pub completed_scans:
        std::collections::BTreeSet<kairos_primitives::reference::ReferenceSourceId>,
    pub removed_scans: std::collections::BTreeSet<kairos_primitives::reference::ReferenceSourceId>,
}

impl SourceChanges {
    /// A pending source or one of its scoped scans must not be fetched again
    /// before its completed staging has been finalized by the Actor.
    pub(crate) fn affects_source(&self, source_id: &str) -> bool {
        self.completed_scans
            .iter()
            .chain(&self.removed_scans)
            .any(|scan| {
                scan.as_str() == source_id
                    || scan
                        .as_str()
                        .strip_prefix(source_id)
                        .is_some_and(|suffix| suffix.starts_with(':'))
            })
    }
}

pub(crate) struct SourceUpdate {
    pub catalog: ProviderCatalog,
    pub complete: bool,
    pub page_count: usize,
    pub pages_done: Option<u64>,
    pub pages_total: Option<u64>,
    pub records_seen: Option<u64>,
    pub records_changed: Option<u64>,
    pub staged_changes: Option<SourceChanges>,
    pub work_item_id: Option<String>,
    pub scope_id: Option<String>,
    pub scope_kind: Option<String>,
    pub cursor_present: Option<bool>,
}

impl Default for SourceUpdate {
    fn default() -> Self {
        Self {
            catalog: ProviderCatalog::default(),
            complete: true,
            page_count: 0,
            pages_done: None,
            pages_total: None,
            records_seen: None,
            records_changed: None,
            staged_changes: None,
            work_item_id: None,
            scope_id: None,
            scope_kind: None,
            cursor_present: None,
        }
    }
}

impl SourceUpdate {
    pub(crate) fn single(catalog: ProviderCatalog) -> Self {
        let records_seen = Some(catalog.record_count() as u64);
        Self {
            catalog,
            complete: true,
            page_count: 1,
            pages_done: Some(1),
            records_seen,
            ..Self::default()
        }
    }

    pub(crate) fn note_scheduled_work_item(&mut self, work_item: &SourceWorkItem) {
        if self.work_item_id.is_none() {
            self.work_item_id = Some(work_item.work_item_id.clone());
        }
        if self.scope_id.is_none() {
            self.scope_id = work_item.scope.id.clone();
        }
        if self.scope_kind.is_none() {
            self.scope_kind = Some(work_item.scope.kind.as_str().to_owned());
        }
    }
}

#[cfg(test)]
mod tests {
    use crate::domain::{SourceScope, SourceTickBudget, SourceWorkItem, SourceWorkReason};
    use crate::services::sources::SourceUpdate;

    #[test]
    fn pending_scans_block_only_the_owning_source() {
        let changes = super::SourceChanges {
            completed_scans: [kairos_primitives::reference::ReferenceSourceId::new(
                "massive-options:SPY",
            )
            .unwrap()]
            .into_iter()
            .collect(),
            removed_scans: [
                kairos_primitives::reference::ReferenceSourceId::new("binance-spot").unwrap(),
            ]
            .into_iter()
            .collect(),
        };
        assert!(changes.affects_source("massive-options"));
        assert!(changes.affects_source("massive-options:SPY"));
        assert!(changes.affects_source("binance-spot"));
        assert!(!changes.affects_source("massive-option"));
        assert!(!changes.affects_source("massive-options:SP"));
        assert!(!changes.affects_source("binance-usdm-futures"));
    }

    #[test]
    fn source_update_uses_scheduled_work_item_as_default_context() {
        let work_item = SourceWorkItem {
            work_item_id: "binance-spot:provider_catalog".to_owned(),
            source_id: kairos_primitives::reference::ReferenceSourceId::new("binance-spot")
                .unwrap(),
            scope: SourceScope::provider_catalog(),
            reason: SourceWorkReason::ScheduledTick,
            budget: SourceTickBudget::default(),
        };
        let mut update = SourceUpdate::default();

        update.note_scheduled_work_item(&work_item);

        assert_eq!(
            update.work_item_id.as_deref(),
            Some("binance-spot:provider_catalog")
        );
        assert_eq!(update.scope_id, None);
        assert_eq!(update.scope_kind.as_deref(), Some("provider_catalog"));
    }

    #[test]
    fn source_update_keeps_provider_specific_work_item_context() {
        let work_item = SourceWorkItem {
            work_item_id: "massive-options:provider_catalog".to_owned(),
            source_id: kairos_primitives::reference::ReferenceSourceId::new("massive-options")
                .unwrap(),
            scope: SourceScope::provider_catalog(),
            reason: SourceWorkReason::ScheduledTick,
            budget: SourceTickBudget::default(),
        };
        let mut update = SourceUpdate {
            work_item_id: Some(
                "massive-options:underlying_instrument:instrument:equity:US:SPY:common".to_owned(),
            ),
            scope_id: Some("instrument:equity:US:SPY:common".to_owned()),
            scope_kind: Some("underlying_instrument".to_owned()),
            cursor_present: Some(true),
            ..SourceUpdate::default()
        };

        update.note_scheduled_work_item(&work_item);

        assert_eq!(
            update.work_item_id.as_deref(),
            Some("massive-options:underlying_instrument:instrument:equity:US:SPY:common")
        );
        assert_eq!(
            update.scope_id.as_deref(),
            Some("instrument:equity:US:SPY:common")
        );
        assert_eq!(update.scope_kind.as_deref(), Some("underlying_instrument"));
        assert_eq!(update.cursor_present, Some(true));
    }
}
