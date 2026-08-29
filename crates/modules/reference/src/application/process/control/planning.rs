//! User-goal planning for preparing the Reference catalog.

use kairos_primitives::reference::InstrumentKind;
use kairos_reference_contract::{
    ReferenceCatalogActivity, ReferenceCatalogActualScope, ReferenceCatalogAvailability,
    ReferenceCatalogGoal, ReferenceCatalogPreparationProgress, ReferenceCatalogRecommendation,
    ReferenceCatalogRecommendationReason, ReferenceCatalogSetupBlocker,
    ReferenceCatalogSetupOption, ReferenceCatalogSetupPlan, ReferenceCatalogSetupRequest,
    ReferenceCatalogSourceLimitation,
};

use crate::application::ReferenceApplication;
use crate::domain::{SourceHealth, SourceRuntimePhase};
use crate::services::providers::{
    BinanceReferenceSource, HyperliquidProduct, MassiveReferenceSource, OkxProduct,
    ReferenceSourceBinding,
};

#[derive(Clone, Copy)]
struct Candidate {
    binding: ReferenceSourceBinding,
    actual_scope: ReferenceCatalogActualScope,
    reasons: &'static [ReferenceCatalogRecommendationReason],
    limitations: &'static [ReferenceCatalogSourceLimitation],
}

const AUTHORITATIVE_LISTINGS: &[ReferenceCatalogRecommendationReason] =
    &[ReferenceCatalogRecommendationReason::AuthoritativeExchangeListings];
const NATIVE_CATALOG: &[ReferenceCatalogRecommendationReason] =
    &[ReferenceCatalogRecommendationReason::NativeExchangeCatalog];
const SUPPORTED_PRODUCT: &[ReferenceCatalogRecommendationReason] =
    &[ReferenceCatalogRecommendationReason::SupportedProduct];
const COMPLETE_US_EQUITIES: &[ReferenceCatalogSourceLimitation] = &[
    ReferenceCatalogSourceLimitation::SynchronizesCompleteUnitedStatesEquities,
    ReferenceCatalogSourceLimitation::RequiresProviderAccount,
];
const REQUIRES_ACCOUNT: &[ReferenceCatalogSourceLimitation] =
    &[ReferenceCatalogSourceLimitation::RequiresProviderAccount];
const PROVIDER_SPECIFIC: &[ReferenceCatalogSourceLimitation] =
    &[ReferenceCatalogSourceLimitation::ProductIsProviderSpecific];
const BINANCE_STOCKS: &[ReferenceCatalogSourceLimitation] = &[
    ReferenceCatalogSourceLimitation::RequiresProviderAccount,
    ReferenceCatalogSourceLimitation::ProductIsProviderSpecific,
];

impl ReferenceApplication {
    pub(crate) async fn contract_catalog_setup_plan(
        &mut self,
        request: ReferenceCatalogSetupRequest,
    ) -> ReferenceCatalogSetupPlan {
        let model = self.read_model().await;
        catalog_setup_plan(request, model.source_health(), model.outbox_depth())
    }
}

fn catalog_setup_plan(
    request: ReferenceCatalogSetupRequest,
    source_health: &[SourceHealth],
    outbox_depth: usize,
) -> ReferenceCatalogSetupPlan {
    let (candidates, mut blockers) = candidates_for_goal(&request.goal);
    if candidates.is_empty() && blockers.is_empty() {
        blockers.push(ReferenceCatalogSetupBlocker::UnsupportedGoal);
    }

    let options = candidates
        .iter()
        .enumerate()
        .map(|(index, candidate)| setup_option(*candidate, index == 0, source_health))
        .collect::<Vec<_>>();
    let selected_index = options
        .iter()
        .position(|option| option.already_configured)
        .or((!options.is_empty()).then_some(0));
    let selected_health = selected_index.and_then(|index| {
        candidates
            .get(index)
            .and_then(|candidate| health_for(candidate.binding, source_health))
    });

    if selected_index
        .and_then(|index| options.get(index))
        .is_some_and(|option| option.requires_connection && !option.connection_binding_present)
    {
        blockers.push(ReferenceCatalogSetupBlocker::MissingConnectionBinding);
    }

    ReferenceCatalogSetupPlan {
        goal: request.goal,
        availability: catalog_availability(selected_health),
        activity: catalog_activity(selected_health, outbox_depth),
        options,
        recommended_option: (!candidates.is_empty()).then_some(0),
        blockers,
        progress: selected_health.map(preparation_progress),
    }
}

fn candidates_for_goal(
    goal: &ReferenceCatalogGoal,
) -> (Vec<Candidate>, Vec<ReferenceCatalogSetupBlocker>) {
    match goal {
        ReferenceCatalogGoal::ProviderProduct { binding } => (
            vec![candidate(
                ReferenceSourceBinding::from_contract(*binding),
                ReferenceCatalogActualScope::ProviderCatalog,
                SUPPORTED_PRODUCT,
                if matches!(
                    binding,
                    kairos_reference_contract::ReferenceSourceBinding::Binance(
                        kairos_reference_contract::BinanceReferenceSource::Equity
                    )
                ) {
                    BINANCE_STOCKS
                } else {
                    PROVIDER_SPECIFIC
                },
            )],
            Vec::new(),
        ),
        ReferenceCatalogGoal::EquityOptions { underlyings } => {
            if underlyings.is_empty() {
                return (
                    Vec::new(),
                    vec![ReferenceCatalogSetupBlocker::EmptyUnderlyingSelection],
                );
            }
            (
                vec![candidate(
                    ReferenceSourceBinding::Massive(MassiveReferenceSource::Options),
                    ReferenceCatalogActualScope::SelectedUnderlyings,
                    AUTHORITATIVE_LISTINGS,
                    REQUIRES_ACCOUNT,
                )],
                Vec::new(),
            )
        },
        ReferenceCatalogGoal::ExchangeInstruments {
            exchange_id,
            instrument_kind,
        } => {
            let exchange = exchange_id
                .as_str()
                .strip_prefix("exchange:")
                .unwrap_or(exchange_id.as_str())
                .to_ascii_lowercase();
            (exchange_candidates(&exchange, *instrument_kind), Vec::new())
        },
    }
}

fn exchange_candidates(exchange: &str, kind: InstrumentKind) -> Vec<Candidate> {
    match (exchange, kind) {
        (
            "nasdaq" | "xnas" | "nyse" | "xnys" | "amex" | "xase" | "arca" | "arcx",
            InstrumentKind::Equity,
        ) => vec![candidate(
            ReferenceSourceBinding::Massive(MassiveReferenceSource::Equity),
            ReferenceCatalogActualScope::CompleteUnitedStatesEquities,
            AUTHORITATIVE_LISTINGS,
            COMPLETE_US_EQUITIES,
        )],
        ("binance", InstrumentKind::Spot) => vec![native_candidate(
            ReferenceSourceBinding::Binance(BinanceReferenceSource::Spot),
        )],
        ("binance", InstrumentKind::Perpetual) => vec![
            native_candidate(ReferenceSourceBinding::Binance(
                BinanceReferenceSource::UsdMFutures,
            )),
            native_candidate(ReferenceSourceBinding::Binance(
                BinanceReferenceSource::CoinMFutures,
            )),
        ],
        ("binance", InstrumentKind::Future) => vec![
            native_candidate(ReferenceSourceBinding::Binance(
                BinanceReferenceSource::UsdMFutures,
            )),
            native_candidate(ReferenceSourceBinding::Binance(
                BinanceReferenceSource::CoinMFutures,
            )),
        ],
        ("binance", InstrumentKind::Option) => vec![native_candidate(
            ReferenceSourceBinding::Binance(BinanceReferenceSource::Options),
        )],
        ("binance", InstrumentKind::Equity) => vec![candidate(
            ReferenceSourceBinding::Binance(BinanceReferenceSource::Equity),
            ReferenceCatalogActualScope::ProviderCatalog,
            SUPPORTED_PRODUCT,
            &[
                ReferenceCatalogSourceLimitation::RequiresProviderAccount,
                ReferenceCatalogSourceLimitation::ProductIsProviderSpecific,
            ],
        )],
        ("okx", InstrumentKind::Spot) => vec![native_candidate(ReferenceSourceBinding::Okx(
            OkxProduct::Spot,
        ))],
        ("okx", InstrumentKind::Perpetual) => vec![native_candidate(ReferenceSourceBinding::Okx(
            OkxProduct::Swap,
        ))],
        ("okx", InstrumentKind::Future) => vec![native_candidate(ReferenceSourceBinding::Okx(
            OkxProduct::Futures,
        ))],
        ("okx", InstrumentKind::Option) => vec![native_candidate(ReferenceSourceBinding::Okx(
            OkxProduct::Option,
        ))],
        ("hyperliquid", InstrumentKind::Spot) => vec![native_candidate(
            ReferenceSourceBinding::Hyperliquid(HyperliquidProduct::Spot),
        )],
        ("hyperliquid", InstrumentKind::Perpetual) => vec![native_candidate(
            ReferenceSourceBinding::Hyperliquid(HyperliquidProduct::Perpetual),
        )],
        _ => Vec::new(),
    }
}

const fn candidate(
    binding: ReferenceSourceBinding,
    actual_scope: ReferenceCatalogActualScope,
    reasons: &'static [ReferenceCatalogRecommendationReason],
    limitations: &'static [ReferenceCatalogSourceLimitation],
) -> Candidate {
    Candidate {
        binding,
        actual_scope,
        reasons,
        limitations,
    }
}

const fn native_candidate(binding: ReferenceSourceBinding) -> Candidate {
    candidate(
        binding,
        ReferenceCatalogActualScope::RequestedExchange,
        NATIVE_CATALOG,
        PROVIDER_SPECIFIC,
    )
}

fn setup_option(
    candidate: Candidate,
    recommended: bool,
    source_health: &[SourceHealth],
) -> ReferenceCatalogSetupOption {
    let health = health_for(candidate.binding, source_health);
    let already_configured = health.is_some_and(|health| health.definition.is_some());
    let connection_binding_present = health
        .and_then(|health| health.definition.as_ref())
        .is_some_and(|definition| definition.credential_binding.is_some());
    let mut reasons = candidate.reasons.to_vec();
    if already_configured {
        reasons.push(ReferenceCatalogRecommendationReason::ExistingConfiguration);
    }
    ReferenceCatalogSetupOption {
        binding: candidate.binding.to_contract(),
        recommendation: if recommended {
            ReferenceCatalogRecommendation::Recommended
        } else {
            ReferenceCatalogRecommendation::Alternative
        },
        actual_scope: candidate.actual_scope,
        requires_connection: candidate.binding.requires_credential(),
        connection_binding_present,
        already_configured,
        reasons,
        limitations: candidate.limitations.to_vec(),
    }
}

fn health_for(
    binding: ReferenceSourceBinding,
    source_health: &[SourceHealth],
) -> Option<&SourceHealth> {
    source_health
        .iter()
        .find(|health| health.source_id == binding.source_id())
}

fn catalog_availability(health: Option<&SourceHealth>) -> ReferenceCatalogAvailability {
    let Some(health) = health else {
        return ReferenceCatalogAvailability::NotConfigured;
    };
    if health.definition.is_none() {
        return ReferenceCatalogAvailability::NotConfigured;
    }
    if health.stale {
        return ReferenceCatalogAvailability::Stale;
    }
    match (health.last_success_unix_nanos.is_some(), health.status) {
        (true, SourceRuntimePhase::Degraded | SourceRuntimePhase::Unavailable) => {
            ReferenceCatalogAvailability::PartiallyUsable
        },
        (true, _) => ReferenceCatalogAvailability::Usable,
        (
            false,
            SourceRuntimePhase::Unavailable
            | SourceRuntimePhase::Paused
            | SourceRuntimePhase::Disabled,
        ) => ReferenceCatalogAvailability::Unavailable,
        (false, _) => ReferenceCatalogAvailability::Preparing,
    }
}

fn catalog_activity(
    health: Option<&SourceHealth>,
    outbox_depth: usize,
) -> ReferenceCatalogActivity {
    let Some(health) = health else {
        return ReferenceCatalogActivity::Idle;
    };
    if health.retry_after_unix_nanos.is_some() {
        return ReferenceCatalogActivity::RetryWaiting;
    }
    match health.status {
        SourceRuntimePhase::Scanning | SourceRuntimePhase::Syncing => {
            ReferenceCatalogActivity::Scanning
        },
        SourceRuntimePhase::Promoting => ReferenceCatalogActivity::Promoting,
        SourceRuntimePhase::Registered => ReferenceCatalogActivity::Waiting,
        SourceRuntimePhase::Paused | SourceRuntimePhase::Disabled => {
            ReferenceCatalogActivity::Paused
        },
        _ if outbox_depth > 0 => ReferenceCatalogActivity::Publishing,
        _ => ReferenceCatalogActivity::Idle,
    }
}

fn preparation_progress(health: &SourceHealth) -> ReferenceCatalogPreparationProgress {
    ReferenceCatalogPreparationProgress {
        pages_done: health.progress.pages_done,
        pages_total: health.progress.pages_total,
        records_seen: health.progress.records_seen,
        records_changed: health.progress.records_changed,
        last_success_unix_nanos: health.last_success_unix_nanos,
        retry_after_unix_nanos: health.retry_after_unix_nanos,
    }
}

#[cfg(test)]
mod tests {
    use kairos_primitives::reference::{ExchangeId, InstrumentId, InstrumentKind};
    use kairos_primitives::time::UnixNanos;
    use kairos_reference_contract::{
        BinanceReferenceSource, MassiveReferenceSource, ReferenceCatalogActivity,
        ReferenceCatalogActualScope, ReferenceCatalogAvailability, ReferenceCatalogGoal,
        ReferenceCatalogSetupBlocker, ReferenceCatalogSetupRequest,
        ReferenceCatalogSourceLimitation, ReferenceSourceBinding,
    };

    use super::catalog_setup_plan;
    use crate::domain::{
        SourceHealth, SourceRuntimePhase, SourceRuntimeProgress, SourceRuntimeWorkItem,
    };
    use crate::services::providers::{
        MassiveReferenceSource as DomainMassiveSource,
        ReferenceSourceBinding as DomainSourceBinding,
    };

    #[test]
    fn nasdaq_equities_discloses_complete_us_catalog_and_account_requirement() {
        let plan = catalog_setup_plan(
            exchange_goal("exchange:nasdaq", InstrumentKind::Equity),
            &[],
            0,
        );

        assert_eq!(
            plan.availability,
            ReferenceCatalogAvailability::NotConfigured
        );
        assert_eq!(plan.recommended_option, Some(0));
        assert_eq!(
            plan.options[0].binding,
            ReferenceSourceBinding::Massive(MassiveReferenceSource::Equity)
        );
        assert_eq!(
            plan.options[0].actual_scope,
            ReferenceCatalogActualScope::CompleteUnitedStatesEquities
        );
        assert!(
            plan.options[0].limitations.contains(
                &ReferenceCatalogSourceLimitation::SynchronizesCompleteUnitedStatesEquities
            )
        );
        assert_eq!(
            plan.blockers,
            vec![ReferenceCatalogSetupBlocker::MissingConnectionBinding]
        );
    }

    #[test]
    fn configured_source_reports_scan_progress_and_usable_last_known_catalog() {
        let binding = DomainSourceBinding::Massive(DomainMassiveSource::Equity);
        let mut health = health(binding);
        health.status = SourceRuntimePhase::Scanning;
        health.progress = SourceRuntimeProgress::paged(Some(2), Some(5), Some(400), Some(380));
        health.last_success_unix_nanos = Some(UnixNanos::new(123));

        let plan = catalog_setup_plan(
            exchange_goal("exchange:nyse", InstrumentKind::Equity),
            &[health],
            0,
        );

        assert_eq!(plan.availability, ReferenceCatalogAvailability::Usable);
        assert_eq!(plan.activity, ReferenceCatalogActivity::Scanning);
        assert_eq!(plan.progress.unwrap().pages_done, Some(2));
        assert!(plan.options[0].already_configured);
        assert!(plan.options[0].connection_binding_present);
        assert!(plan.blockers.is_empty());
    }

    #[test]
    fn unsupported_exchange_and_empty_option_selection_are_explicit() {
        let unsupported = catalog_setup_plan(
            exchange_goal("exchange:unknown", InstrumentKind::Index),
            &[],
            0,
        );
        assert_eq!(
            unsupported.blockers,
            vec![ReferenceCatalogSetupBlocker::UnsupportedGoal]
        );

        let empty_options = catalog_setup_plan(
            ReferenceCatalogSetupRequest {
                goal: ReferenceCatalogGoal::EquityOptions {
                    underlyings: Vec::new(),
                },
            },
            &[],
            0,
        );
        assert_eq!(
            empty_options.blockers,
            vec![ReferenceCatalogSetupBlocker::EmptyUnderlyingSelection]
        );
    }

    #[test]
    fn selected_equity_option_underlyings_use_scoped_massive_catalog() {
        let plan = catalog_setup_plan(
            ReferenceCatalogSetupRequest {
                goal: ReferenceCatalogGoal::EquityOptions {
                    underlyings: vec![
                        InstrumentId::new("instrument:equity:US:AAPL:common").unwrap(),
                    ],
                },
            },
            &[],
            0,
        );
        assert_eq!(
            plan.options[0].actual_scope,
            ReferenceCatalogActualScope::SelectedUnderlyings
        );
    }

    #[test]
    fn binance_equity_is_planned_as_provider_catalog_not_listing_exchange() {
        let plan = catalog_setup_plan(
            ReferenceCatalogSetupRequest {
                goal: ReferenceCatalogGoal::ProviderProduct {
                    binding: ReferenceSourceBinding::Binance(BinanceReferenceSource::Equity),
                },
            },
            &[],
            0,
        );

        assert_eq!(
            plan.options[0].actual_scope,
            ReferenceCatalogActualScope::ProviderCatalog
        );
        assert_eq!(
            plan.options[0].binding,
            ReferenceSourceBinding::Binance(BinanceReferenceSource::Equity)
        );
        assert!(
            plan.options[0]
                .limitations
                .contains(&ReferenceCatalogSourceLimitation::RequiresProviderAccount)
        );
        assert!(
            plan.options[0]
                .limitations
                .contains(&ReferenceCatalogSourceLimitation::ProductIsProviderSpecific)
        );
    }

    fn exchange_goal(exchange: &str, kind: InstrumentKind) -> ReferenceCatalogSetupRequest {
        ReferenceCatalogSetupRequest {
            goal: ReferenceCatalogGoal::ExchangeInstruments {
                exchange_id: ExchangeId::new(exchange).unwrap(),
                instrument_kind: kind,
            },
        }
    }

    fn health(binding: DomainSourceBinding) -> SourceHealth {
        let definition = binding
            .definition(
                crate::domain::SourceScope::global(),
                crate::domain::SourceDesiredState::Enabled,
                Some(crate::domain::SourceCredentialBinding::new("massive-account").unwrap()),
            )
            .unwrap();
        SourceHealth {
            source_id: binding.source_id().to_owned(),
            definition: Some(definition),
            status: SourceRuntimePhase::Registered,
            progress: SourceRuntimeProgress::default(),
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
}
