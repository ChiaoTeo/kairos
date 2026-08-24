use crate::domain::{ProviderCatalog, SourceWorkItem};

pub(crate) struct SourceUpdate {
    pub catalog: ProviderCatalog,
    pub complete: bool,
    pub page_count: usize,
    pub pages_done: Option<u64>,
    pub pages_total: Option<u64>,
    pub records_seen: Option<u64>,
    pub records_changed: Option<u64>,
    pub facts_persisted: bool,
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
            facts_persisted: false,
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
    fn source_update_uses_scheduled_work_item_as_default_context() {
        let work_item = SourceWorkItem {
            work_item_id: "binance-spot:provider_catalog".to_owned(),
            source_id: "binance-spot".to_owned(),
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
            source_id: "massive-options".to_owned(),
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
