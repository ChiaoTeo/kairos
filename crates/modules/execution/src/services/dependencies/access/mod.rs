//! Infrastructure access shared by execution planning and order admission.

use super::*;

pub(super) struct ExecutionDependencyAccess {
    pub(super) accounts: BTreeMap<String, PathBuf>,
    pub(super) account_snapshots: BTreeMap<String, PathBuf>,
    pub(super) market_snapshot: Option<PathBuf>,
    pub(super) market_source_id: String,
    pub(super) risk: Option<PathBuf>,
    pub(super) risk_snapshot: Option<PathBuf>,
    pub(super) risk_actor_id: Option<String>,
    pub(super) dependency_watermarks: DependencyWatermarks,
    pub(super) projection: DependencyProjectionRuntime,
}

impl ExecutionDependencyAccess {
    pub(super) fn risk_reservations_adapter(
        &self,
        reservation_ttl_nanos: u64,
        skip_backtest_risk_authorization: bool,
    ) -> Result<SocketExecutionRiskReservations, String> {
        Ok(SocketExecutionRiskReservations::new(
            self.risk
                .clone()
                .ok_or_else(|| "risk endpoint is not configured".to_string())?,
            self.risk_snapshot
                .clone()
                .ok_or_else(|| "risk mmap snapshot is not configured".to_string())?,
            self.risk_actor_id
                .clone()
                .ok_or_else(|| "risk mmap actor_id is not configured".to_string())?,
            reservation_ttl_nanos,
            skip_backtest_risk_authorization,
        ))
    }
    /// Backtest market events are delivered directly to the deterministic
    /// simulator.  They may be Bars without a live Quote snapshot, so the
    /// live quote projection must not reject an otherwise valid intent.
    pub(super) fn without_market_snapshot(mut self) -> Self {
        self.market_snapshot = None;
        self
    }

    pub(super) fn from_manifest(path: impl AsRef<Path>) -> Result<Self, String> {
        let manifest_path = path.as_ref().to_path_buf();
        let value: Value = serde_json::from_slice(
            &std::fs::read(&manifest_path)
                .map_err(|error| format!("read endpoint manifest: {error}"))?,
        )
        .map_err(|error| format!("decode endpoint manifest: {error}"))?;
        let mut accounts = BTreeMap::new();
        let mut account_snapshots = BTreeMap::new();
        for (account_id, endpoint) in value
            .get("accounts")
            .and_then(Value::as_object)
            .ok_or_else(|| "endpoint manifest has no accounts".to_string())?
        {
            let socket = endpoint
                .get("socket")
                .and_then(Value::as_str)
                .ok_or_else(|| format!("account {account_id} has no socket"))?;
            accounts.insert(account_id.clone(), PathBuf::from(socket));
            let snapshot = endpoint
                .get("snapshot")
                .and_then(Value::as_str)
                .ok_or_else(|| format!("account {account_id} has no snapshot"))?;
            account_snapshots.insert(account_id.clone(), PathBuf::from(snapshot));
        }
        let components = value.get("components").and_then(Value::as_object);
        let endpoint = |name: &str| {
            components
                .and_then(|items| items.get(name))
                .and_then(|item| item.get("socket"))
                .and_then(Value::as_str)
                .map(PathBuf::from)
        };
        let risk_snapshot = components
            .and_then(|items| items.get("risk"))
            .and_then(|item| item.get("snapshot"))
            .and_then(Value::as_str)
            .map(PathBuf::from);
        let risk_actor_id = components
            .and_then(|items| items.get("risk"))
            .and_then(|item| item.get("actor_id"))
            .and_then(Value::as_str)
            .map(str::to_owned)
            .or_else(|| {
                value
                    .get("instance_id")
                    .and_then(Value::as_str)
                    .map(|instance_id| format!("risk:{instance_id}"))
            });
        let instance_root = manifest_path.parent().map(Path::to_path_buf);
        let market_snapshot = instance_root
            .clone()
            .map(|root| root.join("snapshots").join("market").join("market-shared"));
        let market_source_id = components
            .and_then(|items| items.get("market"))
            .and_then(|item| item.get("source_id"))
            .and_then(Value::as_str)
            .unwrap_or("default")
            .to_owned();
        let reference_database = components
            .and_then(|items| items.get("reference"))
            .and_then(|item| item.get("database"))
            .and_then(Value::as_str)
            .map(PathBuf::from);
        let reference_actor_id = components
            .and_then(|items| items.get("reference"))
            .and_then(|item| item.get("actor_id"))
            .and_then(Value::as_str)
            .map(str::to_owned);
        let projection = DependencyProjectionRuntime::start(
            &accounts,
            &account_snapshots,
            market_snapshot.as_deref(),
            reference_database.clone(),
            reference_actor_id.clone(),
            endpoint("risk"),
        );
        Ok(Self {
            accounts,
            account_snapshots,
            market_snapshot,
            market_source_id,
            risk: endpoint("risk"),
            risk_snapshot,
            risk_actor_id,
            dependency_watermarks: DependencyWatermarks::default(),
            projection,
        })
    }

    pub(super) fn account_projection(&self, account_id: &str) -> Result<AccountProjection, String> {
        self.projection.account(account_id)
    }

    pub(super) fn reference_projection(&self) -> Result<ReferenceProjection, String> {
        self.projection.reference()
    }

    pub(super) fn read_market_quote(
        &self,
        market_id: Option<&str>,
        instrument_id: &str,
    ) -> Result<Option<(MarketQuote, u64)>, String> {
        let Some(root) = self.market_snapshot.as_ref() else {
            return Ok(None);
        };
        let Some(market_id) = market_id else {
            return Ok(None);
        };
        let key = kairos_market_contract::MarketViewKey::new(
            market_id,
            self.market_source_id.clone(),
            kairos_market_contract::MarketViewKind::Quote,
            None::<String>,
        )
        .map_err(|error| error.to_string())?;
        let frame = kairos_market_contract::MarketViewReader::open(root, key)
            .and_then(|reader| reader.read())
            .map_err(|error| error.to_string())?;
        let quote = frame
            .quote()
            .map_err(|error| error.to_string())?
            .quote()
            .value();
        if !quote.instrument_id().eq_ignore_ascii_case(instrument_id) {
            return Ok(None);
        }
        let observed_market_id = quote.scope().market_id().ok_or_else(|| {
            "Execution quote dependency requires a market-scoped Market view".to_string()
        })?;
        if !observed_market_id.eq_ignore_ascii_case(market_id) {
            return Ok(None);
        }
        let decimal =
            |value: Option<&kairos_protocol::generated::kairos::common::v_2::Decimal64>| {
                value.map(|value| {
                    let scale = value.scale() as usize;
                    let raw = value.mantissa().to_string();
                    if scale == 0 {
                        return raw;
                    }
                    let negative = raw.starts_with('-');
                    let digits = raw.trim_start_matches('-');
                    let padded = format!("{:0>width$}", digits, width = scale + 1);
                    let split = padded.len() - scale;
                    format!(
                        "{}{}.{}",
                        if negative { "-" } else { "" },
                        &padded[..split],
                        &padded[split..]
                    )
                })
            };
        Ok(Some((
            MarketQuote {
                market_id: observed_market_id.to_owned(),
                instrument_id: quote.instrument_id().to_owned(),
                bid_price: decimal(quote.bid_price()),
                ask_price: decimal(quote.ask_price()),
                observed_at_unix_nanos: quote.source_observed_at_unix_nanos(),
            },
            frame.generation(),
        )))
    }

    pub(super) fn read_market_quotes_for_orders(
        &self,
        orders: &[SubmitOrder],
    ) -> Result<Vec<MarketQuote>, String> {
        orders
            .iter()
            .filter_map(|order| {
                self.read_market_quote(order.market_id.as_deref(), order.instrument_id.as_str())
                    .transpose()
            })
            .map(|result| result.map(|(quote, _)| quote))
            .collect()
    }

    pub(super) fn refresh_watermarks(&mut self) {
        self.dependency_watermarks = self.projection.watermarks();
    }

    /// Backtest commands are serialized by the StrategyHost. Refresh the
    /// account projection synchronously at that barrier so a fill settled by
    /// Account is visible to the very next target-position intent.
    pub(super) fn refresh_account_projections(&mut self) -> Result<(), String> {
        self.projection
            .refresh_accounts(&self.accounts, &self.account_snapshots)
    }

    pub(super) fn reference_market(
        &self,
        market_id: Option<&str>,
        instrument_id: &str,
    ) -> Result<ReferenceMarket, String> {
        let projected = self.reference_projection()?;
        let markets = projected
            .markets
            .into_iter()
            .filter(|value| {
                market_id.map_or_else(
                    || {
                        value.instrument_id == instrument_id
                            && matches!(value.status.as_str(), "active" | "trading")
                    },
                    |market_id| value.market_id == market_id,
                )
            })
            .collect::<Vec<_>>();
        let [market] = markets.as_slice() else {
            return Err(format!(
                "Reference market resolution expected one match for {instrument_id}, found {}",
                markets.len()
            ));
        };
        Ok(market.clone())
    }

    pub(super) fn health(&self, account_id: &str) -> Result<(), String> {
        let projection = self.account_projection(account_id)?;
        let response = projection.health;
        if response.status != "ready" || response.lease_valid == Some(false) {
            return Err(format!("account {account_id} is not ready"));
        }
        Ok(())
    }
}
