//! Safe source runtime error summaries.

use crate::domain::{
    ReferenceError, ReferenceSourceDefinition, SourceRuntimeError, SourceScopeKind,
    SourceSyncPolicy,
};

pub(crate) fn source_runtime_error(error: &ReferenceError) -> SourceRuntimeError {
    let (record_kind, record_id) = error
        .record_identity()
        .map(|(kind, id)| (Some(kind.to_owned()), Some(id.to_owned())))
        .unwrap_or((None, None));
    SourceRuntimeError {
        code: error.code().to_owned(),
        retryable: error.retryable(),
        record_kind,
        record_id,
        message: safe_error_message(error),
    }
}

pub(crate) fn source_activation_unavailable_error(
    definition: &ReferenceSourceDefinition,
) -> SourceRuntimeError {
    let scoped_massive_options = definition.provider_id.as_str() == "massive"
        && definition.provider_product.as_deref() == Some("options")
        && definition.sync_policy == SourceSyncPolicy::ScopedSnapshot;
    let credentialed_source_without_binding = definition.credential_binding.is_none()
        && (matches!(definition.provider_product.as_deref(), Some("equity"))
            || scoped_massive_options);
    let code = if credentialed_source_without_binding {
        "reference.source_credential_binding_missing"
    } else if scoped_massive_options {
        "reference.source_adapter_unavailable"
    } else if definition.sync_policy != SourceSyncPolicy::FullSnapshot
        || definition.scope.kind != SourceScopeKind::Global
    {
        "reference.source_activation_unsupported"
    } else {
        "reference.source_adapter_unavailable"
    };
    let scope_id = definition
        .scope
        .id
        .as_deref()
        .map(|value| format!(" scope_id={value}"))
        .unwrap_or_default();
    SourceRuntimeError {
        code: code.to_owned(),
        retryable: false,
        record_kind: Some("reference_source".to_owned()),
        record_id: Some(definition.source_id.to_string()),
        message: format!(
            "reference source activation is not available: provider_id={} provider_product={} sync_policy={} scope_kind={}{}",
            definition.provider_id,
            definition.provider_product.as_deref().unwrap_or("unknown"),
            definition.sync_policy.as_str(),
            definition.scope.kind.as_str(),
            scope_id
        ),
    }
}

fn safe_error_message(error: &ReferenceError) -> String {
    let value = error.to_string();
    const MAX_LEN: usize = 240;
    if value.len() <= MAX_LEN {
        value
    } else {
        format!("{}...", value.chars().take(MAX_LEN).collect::<String>())
    }
}

#[cfg(test)]
mod tests {
    use crate::domain::{
        ReferenceError, ReferenceSourceDefinition, SourceScope, SourceScopeKind, SourceSyncPolicy,
    };

    use super::{source_activation_unavailable_error, source_runtime_error};

    #[test]
    fn source_runtime_error_keeps_safe_identity_and_code() {
        let error = ReferenceError::DuplicateId {
            record_kind: "market".into(),
            record_id: "market:BTC-USDT".into(),
        };

        let summary = source_runtime_error(&error);

        assert_eq!(summary.code, "reference.duplicate_id");
        assert!(!summary.retryable);
        assert_eq!(summary.record_kind.as_deref(), Some("market"));
        assert_eq!(summary.record_id.as_deref(), Some("market:BTC-USDT"));
        assert!(summary.message.contains("duplicate market id"));
    }

    #[test]
    fn activation_error_explains_unsupported_scoped_source() {
        let definition = ReferenceSourceDefinition {
            source_id: kairos_primitives::integration::ProviderId::new("massive-options").unwrap(),
            provider_id: kairos_primitives::integration::ProviderId::new("massive").unwrap(),
            provider_product: Some(
                kairos_primitives::integration::ProviderProductCode::new("options").unwrap(),
            ),
            scope: SourceScope {
                kind: SourceScopeKind::UnderlyingInstrument,
                id: Some("instrument:equity:US:SPY:common".into()),
            },
            desired_state: Default::default(),
            credential_binding: None,
            sync_policy: SourceSyncPolicy::ScopedSnapshot,
        };

        let summary = source_activation_unavailable_error(&definition);

        assert_eq!(summary.code, "reference.source_activation_unsupported");
        assert_eq!(summary.record_kind.as_deref(), Some("reference_source"));
        assert_eq!(summary.record_id.as_deref(), Some("massive-options"));
        assert!(summary.message.contains("provider_id=massive"));
        assert!(summary.message.contains("scope_kind=underlying_instrument"));
    }
}
