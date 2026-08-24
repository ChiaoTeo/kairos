//! Pure operational diagnostics derived from runtime snapshots.

use kairos_reference_contract::{
    ReferenceCatalogIntegrityStatus, ReferenceDiagnostic, ReferenceDiagnosticSeverity,
    ReferenceSourceDesiredState, ReferenceSourcePhase, ReferenceSourceRuntimeStatus,
    ReferenceSourceSyncPolicy,
};

use crate::application::ReferenceAppErrorSummary;

const PUBLICATION_BACKLOG_DEGRADED_THRESHOLD: usize = 1;

pub(crate) fn runtime_diagnostics(
    sources: &[ReferenceSourceRuntimeStatus],
    pending_publication_count: usize,
    publication_error: Option<&ReferenceAppErrorSummary>,
    catalog_integrity: &ReferenceCatalogIntegrityStatus,
) -> Vec<ReferenceDiagnostic> {
    let mut diagnostics = Vec::new();
    for source in sources {
        let source_id = source.source_id.to_string();
        match source.phase {
            ReferenceSourcePhase::Disabled => {
                let (message, next_action) = disabled_source_guidance(source);
                diagnostics.push(ReferenceDiagnostic {
                    severity: ReferenceDiagnosticSeverity::Info,
                    code: "reference.source.disabled".into(),
                    message,
                    next_action: Some(next_action),
                    source_id: Some(source.source_id.clone()),
                });
            },
            ReferenceSourcePhase::Registered => {
                let (message, next_action) = registered_source_guidance(source);
                diagnostics.push(ReferenceDiagnostic {
                    severity: ReferenceDiagnosticSeverity::Warn,
                    code: "reference.source.registered".into(),
                    message,
                    next_action: Some(next_action),
                    source_id: Some(source.source_id.clone()),
                });
            },
            ReferenceSourcePhase::Paused => diagnostics.push(ReferenceDiagnostic {
                severity: ReferenceDiagnosticSeverity::Warn,
                code: "reference.source.paused".into(),
                message: format!("Reference source {source_id} is paused"),
                next_action: Some(format!("kairos-reference providers resume {source_id}")),
                source_id: Some(source.source_id.clone()),
            }),
            ReferenceSourcePhase::Scanning | ReferenceSourcePhase::Syncing => {
                diagnostics.push(ReferenceDiagnostic {
                    severity: ReferenceDiagnosticSeverity::Info,
                    code: "reference.source.scanning".into(),
                    message: format!(
                        "Reference source {source_id} is scanning{}",
                        source_work_item_hint(source)
                    ),
                    next_action: Some(format!(
                        "wait for the next tick or run `kairos-reference providers show {source_id}`"
                    )),
                    source_id: Some(source.source_id.clone()),
                });
            },
            ReferenceSourcePhase::Promoting => diagnostics.push(ReferenceDiagnostic {
                severity: ReferenceDiagnosticSeverity::Info,
                code: "reference.source.promoting".into(),
                message: format!(
                    "Reference source {source_id} has a complete candidate entering catalog reconciliation{}",
                    source_work_item_hint(source)
                ),
                next_action: Some(format!(
                    "wait for reconcile to complete or run `kairos-reference providers show {source_id}`"
                )),
                source_id: Some(source.source_id.clone()),
            }),
            ReferenceSourcePhase::Unavailable => diagnostics.push(ReferenceDiagnostic {
                severity: ReferenceDiagnosticSeverity::Error,
                code: "reference.source.unavailable".into(),
                message: unavailable_source_message(source, &source_id),
                next_action: Some(format!(
                    "check credentials/connectivity, then run `kairos-reference refresh --source {source_id}`"
                )),
                source_id: Some(source.source_id.clone()),
            }),
            ReferenceSourcePhase::Degraded if source.stale || source.consecutive_failures > 0 => {
                diagnostics.push(ReferenceDiagnostic {
                    severity: ReferenceDiagnosticSeverity::Warn,
                    code: "reference.source.degraded".into(),
                    message: degraded_source_message(source, &source_id),
                    next_action: Some(format!(
                        "inspect logs or run `kairos-reference providers show {source_id}`"
                    )),
                    source_id: Some(source.source_id.clone()),
                });
            },
            _ => {},
        }
    }
    if publication_backlog_degraded(pending_publication_count) {
        diagnostics.push(ReferenceDiagnostic {
            severity: ReferenceDiagnosticSeverity::Warn,
            code: "reference.publication.backlog".into(),
            message: format!(
                "Reference has {pending_publication_count} pending publication event(s)"
            ),
            next_action: Some("kairos-reference publish".into()),
            source_id: None,
        });
    }
    if let Some(error) = publication_error {
        diagnostics.push(ReferenceDiagnostic {
            severity: ReferenceDiagnosticSeverity::Warn,
            code: "reference.publication.failed".into(),
            message: format!(
                "Reference publication recently failed: {} ({})",
                error.message, error.code
            ),
            next_action: Some(
                "inspect publication logs, then run `kairos-reference publish`".into(),
            ),
            source_id: None,
        });
    }
    if catalog_integrity.degraded {
        diagnostics.push(ReferenceDiagnostic {
            severity: ReferenceDiagnosticSeverity::Error,
            code: "reference.catalog.integrity".into(),
            message: catalog_integrity_message(catalog_integrity),
            next_action: Some(
                "inspect Reference catalog diagnostics before relying on catalog consumers".into(),
            ),
            source_id: None,
        });
    }
    diagnostics
}

pub(crate) fn publication_backlog_degraded(pending_publication_count: usize) -> bool {
    pending_publication_count >= PUBLICATION_BACKLOG_DEGRADED_THRESHOLD
}

fn unavailable_source_message(source: &ReferenceSourceRuntimeStatus, source_id: &str) -> String {
    if let Some(error) = &source.last_error {
        format!(
            "Reference source {source_id} is unavailable: {} ({}){}{}",
            error.message,
            error.code,
            source_work_item_hint(source),
            source_retry_hint(source)
        )
    } else if source.has_last_known_good {
        format!(
            "Reference source {source_id} is unavailable but has last-known-good data{}{}",
            source_work_item_hint(source),
            source_retry_hint(source)
        )
    } else {
        format!(
            "Reference source {source_id} is unavailable without last-known-good data{}{}",
            source_work_item_hint(source),
            source_retry_hint(source)
        )
    }
}

fn degraded_source_message(source: &ReferenceSourceRuntimeStatus, source_id: &str) -> String {
    if let Some(error) = &source.last_error {
        format!(
            "Reference source {source_id} is degraded: {} ({}){}{}",
            error.message,
            error.code,
            source_work_item_hint(source),
            source_retry_hint(source)
        )
    } else {
        format!(
            "Reference source {source_id} is degraded{}{}",
            source_work_item_hint(source),
            source_retry_hint(source)
        )
    }
}

fn catalog_integrity_message(integrity: &ReferenceCatalogIntegrityStatus) -> String {
    format!(
        "Reference catalog integrity is degraded: missing_equity_market_count={}, legacy_exchange_market_id_count={}, legacy_exchange_listing_id_count={}",
        integrity.missing_equity_market_count,
        integrity.legacy_exchange_market_id_count,
        integrity.legacy_exchange_listing_id_count
    )
}

fn disabled_source_guidance(source: &ReferenceSourceRuntimeStatus) -> (String, String) {
    let source_id = source.source_id.to_string();
    match source.desired_state {
        Some(ReferenceSourceDesiredState::Removed) => (
            format!("Reference source {source_id} is removed"),
            format!("submit or enable a source definition for {source_id}"),
        ),
        _ => (
            format!("Reference source {source_id} is disabled"),
            format!("kairos-reference providers enable {source_id}"),
        ),
    }
}

fn source_work_item_hint(source: &ReferenceSourceRuntimeStatus) -> String {
    let Some(work_item_id) = source.work_item.work_item_id.as_deref() else {
        return String::new();
    };
    let mut hint = format!("; work_item_id={work_item_id}");
    if let Some(scope_kind) = source.work_item.scope_kind.as_deref() {
        hint.push_str(&format!(" scope_kind={scope_kind}"));
    }
    if let Some(scope_id) = source.work_item.scope_id.as_deref() {
        hint.push_str(&format!(" scope_id={scope_id}"));
    }
    if let Some(cursor_present) = source.work_item.cursor_present {
        hint.push_str(&format!(" cursor_present={cursor_present}"));
    }
    if let Some(skip_reason) = source.work_item.skip_reason.as_deref() {
        hint.push_str(&format!(" skip_reason={skip_reason}"));
    }
    hint
}

fn source_retry_hint(source: &ReferenceSourceRuntimeStatus) -> String {
    match (source.retry_backoff_seconds, source.retry_after_unix_nanos) {
        (Some(backoff), Some(retry_after)) => {
            format!(
                "; retry_backoff_seconds={backoff} retry_after_unix_nanos={}",
                retry_after.get()
            )
        },
        (Some(backoff), None) => format!("; retry_backoff_seconds={backoff}"),
        (None, Some(retry_after)) => {
            format!("; retry_after_unix_nanos={}", retry_after.get())
        },
        (None, None) => String::new(),
    }
}

fn registered_source_guidance(source: &ReferenceSourceRuntimeStatus) -> (String, String) {
    let source_id = source.source_id.to_string();
    let definition_hint = source_definition_hint(source);
    let error_suffix = source
        .last_error
        .as_ref()
        .map(|error| format!(": {} ({})", error.message, error.code))
        .unwrap_or_default();
    if matches!(
        source.sync_policy,
        Some(ReferenceSourceSyncPolicy::ScopedSnapshot)
    ) {
        if source.source_id.as_str() == "massive-options"
            && source
                .provider_id
                .as_ref()
                .is_some_and(|value| value.as_str() == "massive")
        {
            if source.credential_binding_present == Some(false) {
                return (
                    format!(
                        "Reference source {source_id} is registered but no credential binding is configured{definition_hint}{error_suffix}"
                    ),
                    format!("submit {source_id} again with a credential binding"),
                );
            }
            return (
                format!(
                    "Reference source {source_id} is registered but no scoped runtime adapter is active{definition_hint}{error_suffix}"
                ),
                format!(
                    "check the credential binding and scope for {source_id}, then submit the source definition again"
                ),
            );
        }
        return (
            format!(
                "Reference source {source_id} is registered but scoped source activation is not available for this source{definition_hint}{error_suffix}"
            ),
            format!("configure source scopes for {source_id} and enable a scoped source adapter"),
        );
    }
    if matches!(
        source.source_id.as_str(),
        "binance-equity" | "massive-equity"
    ) && matches!(
        source.sync_policy,
        Some(ReferenceSourceSyncPolicy::FullSnapshot)
    ) {
        if source.credential_binding_present == Some(false) {
            return (
                format!(
                    "Reference source {source_id} is registered but no credential binding is configured{definition_hint}{error_suffix}"
                ),
                format!("submit {source_id} again with a credential binding"),
            );
        }
        return (
            format!(
                "Reference source {source_id} is registered but no credentialed runtime adapter is active{definition_hint}{error_suffix}"
            ),
            format!(
                "check the credential binding for {source_id} and submit the source definition again"
            ),
        );
    }
    (
        format!(
            "Reference source {source_id} is registered without an active runtime adapter{definition_hint}{error_suffix}"
        ),
        format!("enable a supported provider factory for {source_id}, then restart Reference"),
    )
}

fn source_definition_hint(source: &ReferenceSourceRuntimeStatus) -> String {
    let mut fields = Vec::new();
    if let Some(provider_id) = &source.provider_id {
        fields.push(format!("provider_id={provider_id}"));
    }
    if let Some(policy) = source.sync_policy {
        fields.push(format!("sync_policy={}", source_sync_policy_as_str(policy)));
    }
    if let Some(scope) = &source.scope {
        fields.push(format!("scope_kind={}", scope.kind.as_str()));
        if let Some(scope_id) = scope.id.as_deref() {
            fields.push(format!("scope_id={scope_id}"));
        }
    }
    if let Some(present) = source.credential_binding_present {
        fields.push(format!("credential_binding_present={present}"));
    }
    if fields.is_empty() {
        String::new()
    } else {
        format!("; {}", fields.join(" "))
    }
}

fn source_sync_policy_as_str(policy: ReferenceSourceSyncPolicy) -> &'static str {
    match policy {
        ReferenceSourceSyncPolicy::FullSnapshot => "full_snapshot",
        ReferenceSourceSyncPolicy::PagedSnapshot => "paged_snapshot",
        ReferenceSourceSyncPolicy::ScopedSnapshot => "scoped_snapshot",
        ReferenceSourceSyncPolicy::IncrementalDelta => "incremental_delta",
        ReferenceSourceSyncPolicy::ManualCurated => "manual_curated",
    }
}

#[cfg(test)]
mod tests {
    use kairos_primitives::reference::ReferenceSourceId;
    use kairos_reference_contract::{
        ReferenceCatalogIntegrityStatus, ReferenceSourceDesiredState, ReferenceSourceKind,
        ReferenceSourcePhase, ReferenceSourceProgress, ReferenceSourceProgressKind,
        ReferenceSourceRuntimeError, ReferenceSourceRuntimeStatus, ReferenceSourceScope,
        ReferenceSourceScopeKind, ReferenceSourceSyncPolicy, ReferenceSourceWorkItem,
    };

    use super::{runtime_diagnostics, source_work_item_hint};
    use crate::application::ReferenceAppErrorSummary;

    fn catalog_integrity_ok() -> ReferenceCatalogIntegrityStatus {
        ReferenceCatalogIntegrityStatus::default()
    }

    fn publication_error() -> ReferenceAppErrorSummary {
        ReferenceAppErrorSummary {
            code: "reference.publication_failed".into(),
            retryable: true,
            message: "reference publication failed: missing publisher".into(),
        }
    }

    fn source_status(
        source_id: &str,
        phase: ReferenceSourcePhase,
        has_last_known_good: bool,
    ) -> ReferenceSourceRuntimeStatus {
        ReferenceSourceRuntimeStatus {
            source_id: ReferenceSourceId::new(source_id.to_owned()).unwrap(),
            provider_id: None,
            source_kind: ReferenceSourceKind::Unknown,
            configured: false,
            enabled: true,
            paused: false,
            desired_state: None,
            sync_policy: None,
            scope: None,
            credential_binding_present: None,
            phase,
            progress: ReferenceSourceProgress {
                kind: ReferenceSourceProgressKind::Unknown,
                pages_done: None,
                pages_total: None,
                records_seen: None,
                records_changed: None,
                scope_id: None,
                scope_kind: None,
                cursor_present: None,
            },
            work_item: ReferenceSourceWorkItem::default(),
            last_attempt_unix_nanos: None,
            last_success_unix_nanos: None,
            retry_after_unix_nanos: None,
            retry_backoff_seconds: None,
            consecutive_failures: 0,
            stale: false,
            has_last_known_good,
            last_error: None,
        }
    }

    #[test]
    fn runtime_diagnostics_turn_source_state_into_actions() {
        let diagnostics = runtime_diagnostics(
            &[
                source_status("massive-options", ReferenceSourcePhase::Paused, true),
                source_status("binance-spot", ReferenceSourcePhase::Unavailable, false),
            ],
            3,
            None,
            &catalog_integrity_ok(),
        );

        let codes = diagnostics
            .iter()
            .map(|diagnostic| diagnostic.code.as_str())
            .collect::<Vec<_>>();
        assert_eq!(
            codes,
            vec![
                "reference.source.paused",
                "reference.source.unavailable",
                "reference.publication.backlog"
            ]
        );
        assert_eq!(
            diagnostics[0].next_action.as_deref(),
            Some("kairos-reference providers resume massive-options")
        );
        assert_eq!(
            diagnostics[1].next_action.as_deref(),
            Some(
                "check credentials/connectivity, then run `kairos-reference refresh --source binance-spot`"
            )
        );
        assert_eq!(
            diagnostics[2].next_action.as_deref(),
            Some("kairos-reference publish")
        );
    }

    #[test]
    fn runtime_diagnostics_include_publication_last_error() {
        let error = publication_error();
        let diagnostics = runtime_diagnostics(&[], 0, Some(&error), &catalog_integrity_ok());

        assert_eq!(diagnostics.len(), 1);
        assert_eq!(diagnostics[0].code, "reference.publication.failed");
        assert!(
            diagnostics[0]
                .message
                .contains("reference.publication_failed")
        );
        assert!(diagnostics[0].message.contains("missing publisher"));
        assert_eq!(
            diagnostics[0].next_action.as_deref(),
            Some("inspect publication logs, then run `kairos-reference publish`")
        );
    }

    #[test]
    fn runtime_diagnostics_treat_disabled_source_as_operator_control() {
        let mut disabled = source_status("massive-options", ReferenceSourcePhase::Disabled, true);
        disabled.desired_state = Some(ReferenceSourceDesiredState::Disabled);
        let diagnostics = runtime_diagnostics(
            std::slice::from_ref(&disabled),
            0,
            None,
            &catalog_integrity_ok(),
        );

        assert_eq!(diagnostics[0].code, "reference.source.disabled");
        assert_eq!(
            diagnostics[0].message,
            "Reference source massive-options is disabled"
        );
        assert_eq!(
            diagnostics[0].next_action.as_deref(),
            Some("kairos-reference providers enable massive-options")
        );
    }

    #[test]
    fn runtime_diagnostics_distinguish_removed_source_control_state() {
        let mut removed = source_status("massive-options", ReferenceSourcePhase::Disabled, true);
        removed.desired_state = Some(ReferenceSourceDesiredState::Removed);
        let diagnostics = runtime_diagnostics(
            std::slice::from_ref(&removed),
            0,
            None,
            &catalog_integrity_ok(),
        );

        assert_eq!(diagnostics[0].code, "reference.source.disabled");
        assert_eq!(
            diagnostics[0].message,
            "Reference source massive-options is removed"
        );
        assert_eq!(
            diagnostics[0].next_action.as_deref(),
            Some("submit or enable a source definition for massive-options")
        );
    }

    #[test]
    fn runtime_diagnostics_include_work_item_and_retry_context() {
        let mut syncing = source_status("massive-options", ReferenceSourcePhase::Scanning, false);
        syncing.work_item = ReferenceSourceWorkItem {
            work_item_id: Some(
                "massive-options:underlying_instrument:instrument:equity:US:SPY:common".into(),
            ),
            scope_id: Some("instrument:equity:US:SPY:common".into()),
            scope_kind: Some("underlying_instrument".into()),
            cursor_present: Some(true),
            skip_reason: None,
        };

        let mut degraded = source_status("binance-spot", ReferenceSourcePhase::Degraded, true);
        degraded.stale = true;
        degraded.consecutive_failures = 1;
        degraded.retry_backoff_seconds = Some(10);
        degraded.retry_after_unix_nanos = Some(123.into());

        let diagnostics =
            runtime_diagnostics(&[syncing, degraded], 0, None, &catalog_integrity_ok());

        assert_eq!(diagnostics[0].code, "reference.source.scanning");
        assert!(
            diagnostics[0]
                .message
                .contains("work_item_id=massive-options:underlying_instrument")
        );
        assert!(diagnostics[0].message.contains("cursor_present=true"));
        assert_eq!(diagnostics[1].code, "reference.source.degraded");
        assert!(diagnostics[1].message.contains("retry_backoff_seconds=10"));
        assert!(
            diagnostics[1]
                .message
                .contains("retry_after_unix_nanos=123")
        );
    }

    #[test]
    fn source_work_item_hint_includes_budget_skip_reason() {
        let mut status = source_status("massive-equity", ReferenceSourcePhase::Idle, false);
        status.work_item = ReferenceSourceWorkItem {
            work_item_id: Some("massive-equity:provider_catalog".into()),
            scope_id: Some("massive-equity".into()),
            scope_kind: Some("provider_catalog".into()),
            cursor_present: None,
            skip_reason: Some("tick_source_budget".into()),
        };

        let hint = source_work_item_hint(&status);

        assert!(hint.contains("work_item_id=massive-equity:provider_catalog"));
        assert!(hint.contains("skip_reason=tick_source_budget"));
    }

    #[test]
    fn runtime_diagnostics_explain_registered_source_without_adapter() {
        let diagnostics = runtime_diagnostics(
            &[source_status(
                "massive-options",
                ReferenceSourcePhase::Registered,
                false,
            )],
            0,
            None,
            &catalog_integrity_ok(),
        );

        assert_eq!(diagnostics[0].code, "reference.source.registered");
        assert_eq!(
            diagnostics[0].message,
            "Reference source massive-options is registered without an active runtime adapter"
        );
    }

    #[test]
    fn runtime_diagnostics_explain_registered_scoped_source() {
        let mut status = source_status("massive-options", ReferenceSourcePhase::Registered, false);
        status.provider_id = Some(kairos_primitives::market::Provider::new("massive").unwrap());
        status.source_kind = ReferenceSourceKind::Scoped;
        status.configured = true;
        status.sync_policy = Some(ReferenceSourceSyncPolicy::ScopedSnapshot);
        status.scope = Some(ReferenceSourceScope {
            kind: ReferenceSourceScopeKind::UnderlyingInstrument,
            id: Some("instrument:equity:US:SPY:common".into()),
        });
        status.credential_binding_present = Some(true);

        let diagnostics = runtime_diagnostics(&[status], 0, None, &catalog_integrity_ok());

        assert_eq!(diagnostics[0].code, "reference.source.registered");
        assert!(
            diagnostics[0]
                .message
                .contains("no scoped runtime adapter is active")
        );
        assert!(diagnostics[0].message.contains("provider_id=massive"));
        assert!(
            diagnostics[0]
                .message
                .contains("sync_policy=scoped_snapshot")
        );
        assert!(
            diagnostics[0]
                .message
                .contains("scope_kind=underlying_instrument")
        );
        assert!(
            diagnostics[0]
                .next_action
                .as_deref()
                .unwrap()
                .contains("submit the source definition again")
        );
    }

    #[test]
    fn runtime_diagnostics_explain_registered_credentialed_source_without_binding() {
        let mut status = source_status("binance-equity", ReferenceSourcePhase::Registered, false);
        status.provider_id = Some(kairos_primitives::market::Provider::new("binance").unwrap());
        status.source_kind = ReferenceSourceKind::Global;
        status.configured = true;
        status.sync_policy = Some(ReferenceSourceSyncPolicy::FullSnapshot);
        status.scope = Some(ReferenceSourceScope {
            kind: ReferenceSourceScopeKind::Global,
            id: None,
        });
        status.credential_binding_present = Some(false);

        let diagnostics = runtime_diagnostics(&[status], 0, None, &catalog_integrity_ok());

        assert_eq!(diagnostics[0].code, "reference.source.registered");
        assert!(
            diagnostics[0]
                .message
                .contains("no credential binding is configured")
        );
        assert!(diagnostics[0].message.contains("provider_id=binance"));
        assert!(diagnostics[0].message.contains("sync_policy=full_snapshot"));
        assert!(
            diagnostics[0]
                .message
                .contains("credential_binding_present=false")
        );
        assert!(
            diagnostics[0]
                .next_action
                .as_deref()
                .unwrap()
                .contains("with a credential binding")
        );
    }

    #[test]
    fn runtime_diagnostics_include_registered_activation_error() {
        let mut status = source_status("custom-source", ReferenceSourcePhase::Registered, false);
        status.last_error = Some(ReferenceSourceRuntimeError {
            code: "reference.provider_failed".into(),
            retryable: false,
            record_kind: None,
            record_id: None,
            message: "credential binding custom.missing is not available".into(),
        });
        let diagnostics = runtime_diagnostics(&[status], 0, None, &catalog_integrity_ok());

        assert_eq!(diagnostics[0].code, "reference.source.registered");
        assert!(
            diagnostics[0]
                .message
                .contains("credential binding custom.missing is not available")
        );
        assert!(diagnostics[0].message.contains("reference.provider_failed"));
    }

    #[test]
    fn runtime_diagnostics_report_catalog_integrity() {
        let status = source_status("binance-spot", ReferenceSourcePhase::Ready, true);
        let integrity = ReferenceCatalogIntegrityStatus {
            degraded: true,
            missing_equity_market_count: 1,
            legacy_exchange_market_id_count: 2,
            legacy_exchange_listing_id_count: 3,
            option_listing_count: 4,
            option_market_count: 5,
        };

        let diagnostics = runtime_diagnostics(&[status], 0, None, &integrity);

        assert_eq!(diagnostics[0].code, "reference.catalog.integrity");
        assert!(
            diagnostics[0]
                .message
                .contains("missing_equity_market_count=1")
        );
        assert!(
            diagnostics[0]
                .message
                .contains("legacy_exchange_market_id_count=2")
        );
        assert!(
            diagnostics[0]
                .message
                .contains("legacy_exchange_listing_id_count=3")
        );
    }

    #[test]
    fn runtime_diagnostics_include_last_error_summary() {
        let mut status = source_status("binance-spot", ReferenceSourcePhase::Unavailable, false);
        status.retry_backoff_seconds = Some(10);
        status.consecutive_failures = 1;
        status.last_error = Some(ReferenceSourceRuntimeError {
            code: "reference.provider_failed".to_owned(),
            retryable: true,
            record_kind: None,
            record_id: None,
            message: "reference provider failed: HTTP 429".to_owned(),
        });

        let diagnostics = runtime_diagnostics(&[status], 1, None, &catalog_integrity_ok());

        assert_eq!(diagnostics[0].code, "reference.source.unavailable");
        assert!(diagnostics[0].message.contains("HTTP 429"));
        assert!(diagnostics[0].message.contains("reference.provider_failed"));
    }
}
