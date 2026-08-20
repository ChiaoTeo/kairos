//! Order-admission context and its application-port implementation.

use super::*;

pub(super) struct OrderAdmissionContext {
    dependencies: ExecutionDependencyAccess,
    allow_backtest_reference_without_projection: bool,
    allow_backtest_balance_without_projection: bool,
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
    pub(super) fn from_manifest(path: impl AsRef<Path>) -> Result<Self, String> {
        Ok(Self {
            dependencies: ExecutionDependencyAccess::from_manifest(path)?,
            allow_backtest_reference_without_projection: false,
            allow_backtest_balance_without_projection: false,
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

    pub(super) fn with_backtest_reference_without_projection(mut self, enabled: bool) -> Self {
        self.allow_backtest_reference_without_projection = enabled;
        self
    }

    pub(super) fn with_backtest_balance_without_projection(mut self, enabled: bool) -> Self {
        self.allow_backtest_balance_without_projection = enabled;
        self
    }

    pub(super) fn risk_reservations_adapter(
        &self,
    ) -> Result<SocketExecutionRiskReservations, String> {
        self.dependencies
            .risk_reservations_adapter(self.reservation_ttl_nanos, false)
    }
}

impl OrderAdmissionContext {
    pub(super) fn dependency_watermarks(&self) -> DependencyWatermarks {
        self.dependency_watermarks.clone()
    }

    pub(super) fn commitment_observation(
        &self,
        account_id: &str,
    ) -> Result<AccountCommitmentObservation, String> {
        self.account_projection(account_id)
            .map(|value| value.commitment_observation)
    }

    pub(super) fn validate_order(
        &mut self,
        request: &SubmitOrder,
        active_commitments: &[OrderCommitment],
    ) -> Result<OrderCommitment, String> {
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
        let reference_market = if !self.allow_backtest_reference_without_projection {
            let market = self.reference_market(
                request.market_id.as_ref().map(MarketId::as_str),
                request.instrument_id.as_str(),
            )?;
            validate_reference_rules(&market, request)?;
            Some(market)
        } else {
            None
        };
        let balances = self
            .account_projection(request.account_id.as_str())?
            .balances;
        let configured_quote_asset = request
            .options
            .quote_asset
            .as_deref()
            .map(kairos_primitives::Currency::new)
            .transpose()
            .map_err(|error| error.to_string())?;
        let asset = match request.side {
            OrderSide::Buy => reference_market
                .as_ref()
                .and_then(|market| market.quote_asset_id.as_ref())
                .map(reference_asset_currency)
                .transpose()?
                .or_else(|| configured_quote_asset.clone()),
            OrderSide::Sell => reference_market
                .as_ref()
                .and_then(|market| market.base_asset_id.as_ref())
                .map(reference_asset_currency)
                .transpose()?,
        };
        let settlement_asset = reference_market
            .as_ref()
            .and_then(|market| market.quote_asset_id.as_ref())
            .map(reference_asset_currency)
            .transpose()?
            .or(configured_quote_asset.clone());
        let mut commitment = if !self.allow_backtest_balance_without_projection {
            let asset = asset.ok_or_else(|| {
                "Reference must define the order commitment asset; symbol suffix inference is forbidden"
                    .to_string()
            })?;
            if request.side == OrderSide::Buy {
                if let (Some(configured), Some(reference)) = (
                    request.options.quote_asset.as_deref(),
                    reference_market.as_ref().and_then(|market| {
                        market
                            .quote_asset_id
                            .as_ref()
                            .and_then(|value| value.as_str().rsplit(':').next())
                    }),
                ) {
                    if !configured.eq_ignore_ascii_case(reference) {
                        return Err(format!(
                            "configured quote asset {configured} disagrees with Reference {reference}"
                        ));
                    }
                }
            }
            let quantity = decimal_quantity(request.quantity)?;
            let needed = if request.side == OrderSide::Buy {
                let price_cap = request.limit_price.ok_or_else(|| {
                    "buy order requires an explicit price cap for commitment calculation"
                        .to_string()
                })?;
                quantity
                    .checked_mul(decimal_price(price_cap)?)
                    .ok_or_else(|| "order notional overflow".to_string())?
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
                    total
                        .checked_add(decimal_money(commitment.amount)?)
                        .ok_or_else(|| "order commitment total overflow".to_string())
                })?;
            let available = find_available(&balances, &asset)?
                .ok_or_else(|| format!("no available balance for {asset}"))?;
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
                Money::new(request.quantity.mantissa(), request.quantity.scale())
                    .map_err(|error| error.to_string())?,
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
                let positions = self
                    .account_projection(request.account_id.as_str())?
                    .positions;
                let current = find_position(&positions, request.instrument_id.as_str())?
                    .unwrap_or(Decimal::ZERO);
                let reserved = active_commitments
                    .iter()
                    .filter(|value| {
                        value.account_id == request.account_id.as_str()
                            && value.instrument_id == request.instrument_id.as_str()
                            && value.status.consumes_capacity()
                    })
                    .try_fold(Decimal::ZERO, |total, value| -> Result<Decimal, String> {
                        let quantity = decimal_quantity(value.remaining_quantity)?;
                        let signed = if value.side == OrderSide::Buy {
                            quantity
                        } else {
                            -quantity
                        };
                        total
                            .checked_add(signed)
                            .ok_or_else(|| "maker inventory reservation overflow".to_string())
                    })?;
                let request_quantity = decimal_quantity(request.quantity)?;
                let signed_request = if request.side == OrderSide::Buy {
                    request_quantity
                } else {
                    -request_quantity
                };
                let projected = current
                    .checked_add(reserved)
                    .and_then(|value| value.checked_add(signed_request))
                    .ok_or_else(|| "maker inventory projection overflow".to_string())?;
                if projected.abs() > decimal_signed_quantity(max_inventory)?.abs() {
                    return Err(format!(
                        "maker inventory guard exceeded for {}: projected={}, limit={}",
                        request.instrument_id, projected, max_inventory
                    ));
                }
            }
        }
        if self.market_snapshot.is_some() {
            let quotes = self
                .read_market_quote(request.market_id.as_deref(), request.instrument_id.as_str())?
                .map(|(quote, _)| vec![quote])
                .unwrap_or_default();
            validate_quote_freshness(std::slice::from_ref(request), &quotes)?;
        }
        Ok(commitment)
    }

    pub(super) fn risk_authorization_context(
        &mut self,
        request: &SubmitOrder,
        route: &crate::application::ExecutionRouteCandidate,
    ) -> Result<RiskAuthorizationContext, String> {
        let reference_market = self.reference_market(
            request.market_id.as_ref().map(MarketId::as_str),
            request.instrument_id.as_str(),
        )?;
        let account = self.account_projection(request.account_id.as_str())?;
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
            exchange_id: Some(reference_market.exchange_id),
            funding_broker: Some(
                kairos_primitives::BrokerId::new(route.participant_id.clone())
                    .map_err(|error| error.to_string())?,
            ),
            funding_segment: Some(request.segment_key.clone()),
            collateral_asset: request
                .options
                .quote_asset
                .as_deref()
                .map(kairos_primitives::Currency::new)
                .transpose()
                .map_err(|error| error.to_string())?,
        })
    }
}

fn reference_asset_currency(
    asset_id: &kairos_primitives::AssetId,
) -> Result<kairos_primitives::Currency, String> {
    let code = asset_id
        .as_str()
        .rsplit(':')
        .next()
        .filter(|value| !value.is_empty())
        .ok_or_else(|| format!("Reference asset {} has no currency code", asset_id))?;
    kairos_primitives::Currency::new(code).map_err(|error| error.to_string())
}
