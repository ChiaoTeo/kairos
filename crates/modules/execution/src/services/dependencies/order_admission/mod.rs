//! Order-admission context and its application-port implementation.

use super::*;

pub(super) struct OrderAdmissionContext {
    dependencies: ExecutionDependencyAccess,
    allow_backtest_without_reference_state: bool,
    allow_backtest_without_account_state: bool,
    reservation_ttl_nanos: u64,
}

impl std::ops::Deref for OrderAdmissionContext {
    type Target = ExecutionDependencyAccess;

    fn deref(&self) -> &Self::Target {
        &self.dependencies
    }
}

impl std::ops::DerefMut for OrderAdmissionContext {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.dependencies
    }
}

impl OrderAdmissionContext {
    pub(super) fn from_manifest_with_reference_catalog(
        system: &mut kairos_conflux::ConfluxSystem,
        path: impl AsRef<Path>,
        reference: Option<kairos_reference_contract::ReferenceCatalog>,
    ) -> Result<Self, AdmissionError> {
        Ok(Self {
            dependencies: ExecutionDependencyAccess::from_manifest_with_reference_catalog(
                system, path, reference,
            )?,
            allow_backtest_without_reference_state: false,
            allow_backtest_without_account_state: false,
            reservation_ttl_nanos: 60_000_000_000,
        })
    }

    pub(super) fn without_market_snapshot(mut self) -> Self {
        self.dependencies = self.dependencies.without_market_snapshot();
        self
    }

    pub(super) fn with_backtest_reservation_window(mut self) -> Self {
        self.reservation_ttl_nanos = 7 * 24 * 60 * 60 * 1_000_000_000;
        self
    }

    pub(super) fn allow_backtest_without_reference_state(mut self, enabled: bool) -> Self {
        self.allow_backtest_without_reference_state = enabled;
        self
    }

    pub(super) fn allow_backtest_without_account_state(mut self, enabled: bool) -> Self {
        self.allow_backtest_without_account_state = enabled;
        self
    }

    pub(super) fn risk_reservations_adapter(
        &self,
    ) -> Result<SocketExecutionRiskReservations, AdmissionError> {
        self.dependencies
            .risk_reservations_adapter(self.reservation_ttl_nanos, false)
            .map_err(Into::into)
    }
}

impl OrderAdmissionContext {
    pub(super) fn dependency_watermarks(&self) -> DependencyWatermarks {
        self.dependency_watermarks.clone()
    }

    pub(super) fn commitment_observation(
        &self,
        account_id: &str,
    ) -> Result<AccountCommitmentObservation, AdmissionError> {
        self.account_dependency_state(account_id)
            .map(|value| value.commitment_observation)
            .map_err(Into::into)
    }

    pub(super) fn validate_order(
        &mut self,
        request: &SubmitOrder,
        active_commitments: &[OrderCommitment],
    ) -> Result<OrderCommitment, AdmissionError> {
        self.health(request.account_id.as_str())?;
        if request.quantity.mantissa() <= 0 {
            return Err("order quantity must be positive".into());
        }
        if request.order_type == OrderType::Limit
            && request
                .limit_price
                .is_some_and(|price| price.mantissa() <= 0)
        {
            return Err("limit price must be positive".into());
        }
        let reference_market = if !self.allow_backtest_without_reference_state {
            let market = self.reference_market(
                request.market_id.as_ref().map(MarketId::as_str),
                request.instrument_id.as_str(),
            )?;
            validate_reference_rules(
                &crate::domain::ExecutionMarketRules {
                    tradable: matches!(
                        market.market.status.as_str().to_ascii_lowercase().as_str(),
                        "active" | "listed" | "trading"
                    ),
                    minimum_quantity: market.market.trading_rules.minimum_quantity,
                    quantity_tick: market.market.trading_rules.quantity_tick,
                    price_tick: market.market.trading_rules.price_tick,
                    minimum_notional: market.market.trading_rules.minimum_notional,
                },
                request,
            )?;
            Some(market)
        } else {
            None
        };
        let account_dependency_state =
            self.account_dependency_state(request.account_id.as_str())?;
        let balances = &account_dependency_state.balances;
        let configured_quote_asset = request
            .options
            .quote_asset
            .as_deref()
            .map(kairos_primitives::reference::Currency::new)
            .transpose()
            .map_err(|source| AdmissionError::InvalidSemantic {
                field: "quote_asset",
                source,
            })?;
        let asset = match request.side {
            OrderSide::Buy => reference_market
                .as_ref()
                .and_then(|resolution| resolution.market.quote_asset_id.as_ref())
                .map(reference_asset_currency)
                .transpose()?
                .or_else(|| configured_quote_asset.clone()),
            OrderSide::Sell => reference_market
                .as_ref()
                .and_then(|resolution| resolution.market.base_asset_id.as_ref())
                .map(reference_asset_currency)
                .transpose()?,
        };
        let settlement_asset = reference_market
            .as_ref()
            .and_then(|resolution| resolution.market.quote_asset_id.as_ref())
            .map(reference_asset_currency)
            .transpose()?
            .or(configured_quote_asset.clone());
        let derivative_reduce = reference_market.as_ref().is_some_and(|resolution| {
            matches!(
                resolution.instrument.instrument_type,
                InstrumentKind::Perpetual | InstrumentKind::Future | InstrumentKind::Option
            ) && request.options.reduce_only == Some(true)
        });
        let mut commitment = if derivative_reduce {
            closeable_position_commitment(
                request,
                &account_dependency_state.positions,
                active_commitments,
            )?
        } else if !self.allow_backtest_without_account_state {
            let asset = asset.ok_or(AdmissionError::Validation {
                rule: "Reference must define the order commitment asset; symbol suffix inference is forbidden",
            })?;
            if request.side == OrderSide::Buy {
                if let (Some(configured), Some(reference)) = (
                    request.options.quote_asset.as_deref(),
                    reference_market.as_ref().and_then(|resolution| {
                        resolution
                            .market
                            .quote_asset_id
                            .as_ref()
                            .and_then(|value| value.as_str().rsplit(':').next())
                    }),
                ) {
                    if !configured.eq_ignore_ascii_case(reference) {
                        return Err(AdmissionError::validation_context(
                            "configured quote asset disagrees with Reference",
                            format!("configured={configured}, reference={reference}"),
                        ));
                    }
                }
            }
            let quantity = decimal_quantity(request.quantity)?;
            let needed = if request.side == OrderSide::Buy {
                let price_cap = request.limit_price.ok_or(AdmissionError::Validation {
                    rule: "buy order requires an explicit price cap for commitment calculation",
                })?;
                quantity
                    .checked_mul(decimal_price(price_cap)?)
                    .ok_or(AdmissionError::Overflow {
                        operation: "order notional",
                    })?
            } else {
                quantity
            };
            let commitment_resource = CommitmentResource::Asset(asset.clone());
            let committed = active_commitments
                .iter()
                .filter(|commitment| {
                    commitment.consumes_unreflected_physical_capacity()
                        && commitment.account_id == request.account_id
                        && commitment.segment_key == request.segment_key
                        && commitment.resource == commitment_resource
                })
                .try_fold(Decimal::ZERO, |total, commitment| {
                    total.checked_add(decimal_money(commitment.amount)?).ok_or(
                        AdmissionError::Overflow {
                            operation: "order commitment total",
                        },
                    )
                })?;
            let available = find_available(balances, &asset)?.ok_or_else(|| {
                AdmissionError::validation_context(
                    "available balance is missing",
                    asset.to_string(),
                )
            })?;
            ensure_available_capacity(available, committed, needed, &asset)?;
            OrderCommitment::new(
                request.order_id.clone(),
                request.account_id.clone(),
                request.segment_key.clone(),
                request.instrument_id.clone(),
                request.side,
                commitment_resource,
                money_from_decimal(needed)?,
                request.quantity,
                if request.side == OrderSide::Buy {
                    CommitmentBasis::QuotePriceCap {
                        price_cap: request.limit_price.expect("validated price cap"),
                    }
                } else {
                    CommitmentBasis::BaseQuantity
                },
                request
                    .submitted_at_unix_nanos
                    .unwrap_or_else(|| UnixNanos::new(0)),
            )?
        } else {
            OrderCommitment::new(
                request.order_id.clone(),
                request.account_id.clone(),
                request.segment_key.clone(),
                request.instrument_id.clone(),
                request.side,
                CommitmentResource::Instrument(request.instrument_id.clone()),
                Money::new(request.quantity.mantissa(), request.quantity.scale()).map_err(
                    |source| AdmissionError::InvalidSemantic {
                        field: "commitment_amount",
                        source,
                    },
                )?,
                request.quantity,
                CommitmentBasis::SimulationQuantity,
                request
                    .submitted_at_unix_nanos
                    .unwrap_or_else(|| UnixNanos::new(0)),
            )?
        };
        commitment.settlement_asset = settlement_asset;
        if let Some(policy) = request.options.maker.as_ref() {
            if let Some(max_inventory) = policy.max_inventory_abs {
                let current = find_position(
                    &account_dependency_state.positions,
                    request.instrument_id.as_str(),
                )?
                .unwrap_or(Decimal::ZERO);
                let reserved = active_commitments
                    .iter()
                    .filter(|value| {
                        value.account_id == request.account_id.as_str()
                            && value.instrument_id == request.instrument_id.as_str()
                            && value.status.consumes_capacity()
                    })
                    .try_fold(
                        Decimal::ZERO,
                        |total, value| -> Result<Decimal, AdmissionError> {
                            let quantity = decimal_quantity(value.remaining_quantity)?;
                            let signed = if value.side == OrderSide::Buy {
                                quantity
                            } else {
                                -quantity
                            };
                            total
                                .checked_add(signed)
                                .ok_or_else(|| AdmissionError::Overflow {
                                    operation: "maker inventory reservation",
                                })
                        },
                    )?;
                let request_quantity = decimal_quantity(request.quantity)?;
                let signed_request = if request.side == OrderSide::Buy {
                    request_quantity
                } else {
                    -request_quantity
                };
                let resulting_inventory = current
                    .checked_add(reserved)
                    .and_then(|value| value.checked_add(signed_request))
                    .ok_or(AdmissionError::Overflow {
                        operation: "maker inventory calculation",
                    })?;
                if resulting_inventory.abs() > decimal_signed_quantity(max_inventory)?.abs() {
                    return Err(AdmissionError::validation_context(
                        "maker inventory guard exceeded",
                        request.instrument_id.to_string(),
                    ));
                }
            }
        }
        if self.market_snapshot.is_some() {
            let quotes = self
                .read_market_quote(request.market_id.as_deref(), request.instrument_id.as_str())?
                .map(|(quote, _)| vec![quote])
                .unwrap_or_default();
            let business_time = request.submitted_at_unix_nanos.map(UnixNanos::get).ok_or(
                AdmissionError::Validation {
                    rule: "order admission requires explicit business time",
                },
            )?;
            validate_quote_freshness(std::slice::from_ref(request), &quotes, business_time, None)?;
        }
        Ok(commitment)
    }

    pub(super) fn risk_authorization_context(
        &mut self,
        request: &SubmitOrder,
        route: &crate::domain::ExecutionRouteCandidate,
    ) -> Result<RiskAuthorizationContext, AdmissionError> {
        let reference_market = self.reference_market(
            request.market_id.as_ref().map(MarketId::as_str),
            request.instrument_id.as_str(),
        )?;
        let account = self.account_dependency_state(request.account_id.as_str())?;
        let market = if self.market_snapshot.is_some() {
            self.read_market_quote(request.market_id.as_deref(), request.instrument_id.as_str())?
                .map(|(_, generation)| SnapshotWatermark {
                    generation: generation.into(),
                    event_sequence: 0.into(),
                })
        } else {
            None
        };
        let available_margin = request
            .options
            .quote_asset
            .as_deref()
            .map(|asset| find_available(&account.balances, asset))
            .transpose()?
            .flatten()
            .filter(|value| *value > Decimal::ZERO)
            .map(money_from_decimal)
            .transpose()?;
        Ok(RiskAuthorizationContext {
            account: SnapshotWatermark {
                generation: account.health.generation.into(),
                event_sequence: account.health.event_sequence.into(),
            },
            market_is_fresh: self.market_snapshot.is_none() || market.is_some(),
            market,
            available_margin,
            initial_margin_rate_bps: route.initial_margin_rate_bps,
            margin_rule_id: route.margin_rule_id.clone(),
            exchange_id: legacy_exchange_id_for_risk(&reference_market.venue)?,
            funding_broker: Some(route.broker_id.clone()),
            funding_segment: Some(request.segment_key.clone()),
            collateral_asset: request
                .options
                .quote_asset
                .as_deref()
                .map(kairos_primitives::reference::Currency::new)
                .transpose()
                .map_err(|source| AdmissionError::InvalidSemantic {
                    field: "collateral_asset",
                    source,
                })?,
        })
    }
}

fn closeable_position_commitment(
    request: &SubmitOrder,
    positions: &[AccountPositionFact],
    active_commitments: &[OrderCommitment],
) -> Result<OrderCommitment, AdmissionError> {
    let position_side = request
        .options
        .position_side
        .as_deref()
        .unwrap_or("net")
        .parse::<PositionSide>()
        .map_err(|error| {
            AdmissionError::validation_context("invalid position side", error.to_string())
        })?;
    let position = positions
        .iter()
        .find(|position| {
            position.segment_key == request.segment_key.as_str()
                && position
                    .instrument_id
                    .eq_ignore_ascii_case(request.instrument_id.as_str())
                && position.position_side == position_side
        })
        .ok_or_else(|| {
            AdmissionError::validation_context(
                "no closeable position",
                format!(
                    "no closeable {} position for {} on segment {}",
                    position_side.as_str(),
                    request.instrument_id,
                    request.segment_key
                ),
            )
        })?;
    let signed_position = Decimal::try_new(
        position.quantity.mantissa(),
        u32::from(position.quantity.scale()),
    )
    .map_err(|_| AdmissionError::DecimalRepresentation {
        value_kind: "position quantity",
    })?;
    let closeable = match position_side {
        PositionSide::Net if request.side == OrderSide::Sell && signed_position > Decimal::ZERO => {
            signed_position
        },
        PositionSide::Net if request.side == OrderSide::Buy && signed_position < Decimal::ZERO => {
            signed_position.abs()
        },
        PositionSide::Long if request.side == OrderSide::Sell => signed_position.abs(),
        PositionSide::Short if request.side == OrderSide::Buy => signed_position.abs(),
        _ => {
            return Err(AdmissionError::validation_context(
                "order side cannot reduce the observed position",
                request.instrument_id.to_string(),
            ));
        },
    };
    if closeable <= Decimal::ZERO {
        return Err(AdmissionError::validation_context(
            "observed position has no closeable quantity",
            request.instrument_id.to_string(),
        ));
    }
    let resource = CommitmentResource::CloseablePosition {
        instrument_id: request.instrument_id.clone(),
        position_side,
    };
    let committed = active_commitments
        .iter()
        .filter(|commitment| {
            commitment.status.consumes_capacity()
                && commitment.account_id == request.account_id
                && commitment.segment_key == request.segment_key
                && commitment.resource == resource
        })
        .try_fold(Decimal::ZERO, |total, commitment| {
            total
                .checked_add(decimal_quantity(commitment.remaining_quantity)?)
                .ok_or(AdmissionError::Overflow {
                    operation: "closeable-position commitment total",
                })
        })?;
    let requested = decimal_quantity(request.quantity)?;
    let required = committed
        .checked_add(requested)
        .ok_or(AdmissionError::Overflow {
            operation: "closeable-position requirement",
        })?;
    if required > closeable {
        return Err(AdmissionError::validation_context(
            "insufficient closeable position",
            request.instrument_id.to_string(),
        ));
    }
    OrderCommitment::new(
        request.order_id.clone(),
        request.account_id.clone(),
        request.segment_key.clone(),
        request.instrument_id.clone(),
        request.side,
        resource,
        Money::new(request.quantity.mantissa(), request.quantity.scale()).map_err(|source| {
            AdmissionError::InvalidSemantic {
                field: "commitment_amount",
                source,
            }
        })?,
        request.quantity,
        CommitmentBasis::CloseablePositionQuantity,
        request
            .submitted_at_unix_nanos
            .unwrap_or_else(|| UnixNanos::new(0)),
    )
    .map_err(Into::into)
}

fn legacy_exchange_id_for_risk(
    venue: &kairos_reference_contract::Venue,
) -> Result<Option<kairos_primitives::reference::ExchangeId>, AdmissionError> {
    if venue.venue_kind != kairos_reference_contract::VenueKind::RegulatedExchange {
        return Ok(None);
    }
    let key = venue
        .venue_id
        .as_str()
        .strip_prefix("venue:")
        .unwrap_or(venue.venue_id.as_str());
    kairos_primitives::reference::ExchangeId::new(format!("exchange:{key}"))
        .map(Some)
        .map_err(|source| AdmissionError::InvalidSemantic {
            field: "risk_exchange_id_compatibility",
            source,
        })
}

fn reference_asset_currency(
    asset_id: &kairos_primitives::reference::AssetId,
) -> Result<kairos_primitives::reference::Currency, AdmissionError> {
    let code = asset_id
        .as_str()
        .rsplit(':')
        .next()
        .filter(|value| !value.is_empty())
        .ok_or_else(|| {
            AdmissionError::validation_context(
                "Reference asset has no currency code",
                asset_id.to_string(),
            )
        })?;
    kairos_primitives::reference::Currency::new(code).map_err(|source| {
        AdmissionError::InvalidSemantic {
            field: "reference_asset_currency",
            source,
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::domain::ExecutionOrderOptions;

    fn reduce_order(order_id: &str, side: OrderSide, quantity: i64) -> SubmitOrder {
        SubmitOrder {
            order_id: OrderId::new(order_id).unwrap(),
            intent_id: None,
            strategy_id: None,
            account_id: kairos_primitives::account::AccountId::new("main").unwrap(),
            segment_key: kairos_primitives::account::SegmentKey::new("usd-m").unwrap(),
            instrument_id: InstrumentId::new("instrument:btcusdt-perp").unwrap(),
            market_id: None,
            execution_route_id: None,
            side,
            order_type: OrderType::Market,
            quantity: kairos_primitives::decimal::Quantity::new(quantity, 0).unwrap(),
            limit_price: None,
            options: ExecutionOrderOptions {
                reduce_only: Some(true),
                position_side: Some("net".into()),
                ..ExecutionOrderOptions::default()
            },
            submitted_at_unix_nanos: Some(UnixNanos::new(10)),
        }
    }

    fn net_position(quantity: i64) -> AccountPositionFact {
        AccountPositionFact {
            segment_key: "usd-m".into(),
            instrument_id: "instrument:btcusdt-perp".into(),
            position_side: PositionSide::Net,
            quantity: kairos_account_contract::DecimalValue::new(quantity, 0).unwrap(),
        }
    }

    #[test]
    fn derivative_reduce_commitments_prevent_double_close() {
        let first = closeable_position_commitment(
            &reduce_order("close-1", OrderSide::Sell, 6),
            &[net_position(10)],
            &[],
        )
        .unwrap();
        assert_eq!(
            first.resource,
            CommitmentResource::CloseablePosition {
                instrument_id: InstrumentId::new("instrument:btcusdt-perp").unwrap(),
                position_side: PositionSide::Net,
            }
        );
        assert_eq!(first.basis, CommitmentBasis::CloseablePositionQuantity);

        let error = closeable_position_commitment(
            &reduce_order("close-2", OrderSide::Sell, 5),
            &[net_position(10)],
            &[first],
        )
        .unwrap_err();
        assert!(matches!(
            error,
            AdmissionError::ValidationContext {
                rule: "insufficient closeable position",
                ..
            }
        ));
    }

    #[test]
    fn derivative_reduce_side_must_actually_reduce_the_observed_position() {
        let error = closeable_position_commitment(
            &reduce_order("wrong-side", OrderSide::Buy, 1),
            &[net_position(10)],
            &[],
        )
        .unwrap_err();

        assert!(matches!(
            error,
            AdmissionError::ValidationContext {
                rule: "order side cannot reduce the observed position",
                ..
            }
        ));
    }
}
