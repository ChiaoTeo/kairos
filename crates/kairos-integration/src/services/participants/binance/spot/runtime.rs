//! Shared Binance Spot provider/IP execution resources.

use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use crate::services::quota::{SharedFixedWindowQuota, SharedQuotaPriority};
use crate::services::transport::http::{
    AsyncPublicHttpClient, ExchangeError, HttpJsonResponse, PublicHttpClient,
};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum RequestPriority {
    Cancel,
    NewOrder,
    Reconciliation,
    Background,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct QuotaAllocation {
    pub request_weight_per_minute: u32,
    pub cancel_reserve_weight: u32,
}

#[derive(Debug, Default)]
struct QuotaWindow {
    minute: u64,
    used_weight: u32,
}

const CLOCK_SYNC_TTL: Duration = Duration::from_secs(15 * 60);
// Binance produces `/api/v3/time` near response serialization. Anchor the
// returned value to receive time and stay slightly behind the provider:
// recvWindow tolerates lag, while a timestamp >1s ahead is rejected (-1021).
const CLOCK_SAFETY_LAG_MILLIS: u64 = 250;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct ClockSnapshot {
    pub offset_millis: i64,
    pub generation: u64,
    pub round_trip_millis: u64,
}

#[derive(Debug, Default)]
struct ServerClock {
    offset_millis: i64,
    generation: u64,
    round_trip_millis: u64,
    synchronized_at: Option<Instant>,
}

#[derive(Clone)]
pub(crate) struct BinanceSpotProviderRuntime {
    http: PublicHttpClient,
    async_http: AsyncPublicHttpClient,
    allocation: QuotaAllocation,
    quota: Arc<Mutex<QuotaWindow>>,
    shared_quota: Option<Arc<SharedFixedWindowQuota>>,
    clock: Arc<Mutex<ServerClock>>,
    async_clock_sync: Arc<tokio::sync::Mutex<()>>,
    principal_order_quotas: Vec<PrincipalOrderQuota>,
}

#[derive(Clone)]
pub(crate) struct PrincipalOrderQuota {
    pub header_name: &'static str,
    pub quota: Arc<SharedFixedWindowQuota>,
}

impl BinanceSpotProviderRuntime {
    pub(crate) fn new(
        http: PublicHttpClient,
        allocation: QuotaAllocation,
    ) -> Result<Self, ExchangeError> {
        Self::new_with_shared_quota(http, allocation, None)
    }

    pub(crate) fn new_with_shared_quota(
        http: PublicHttpClient,
        allocation: QuotaAllocation,
        shared_quota: Option<SharedFixedWindowQuota>,
    ) -> Result<Self, ExchangeError> {
        if allocation.request_weight_per_minute == 0 {
            return Err(ExchangeError::InvalidRequest(
                "Binance Spot request-weight allocation must be positive".into(),
            ));
        }
        if allocation.cancel_reserve_weight >= allocation.request_weight_per_minute {
            return Err(ExchangeError::InvalidRequest(
                "Binance Spot cancel reserve must be smaller than the request-weight allocation"
                    .into(),
            ));
        }
        let async_http = AsyncPublicHttpClient::new("kairos-integration/binance-spot-provider")?;
        Ok(Self {
            http,
            async_http,
            allocation,
            quota: Arc::new(Mutex::new(QuotaWindow::default())),
            shared_quota: shared_quota.map(Arc::new),
            clock: Arc::new(Mutex::new(ServerClock::default())),
            async_clock_sync: Arc::new(tokio::sync::Mutex::new(())),
            principal_order_quotas: Vec::new(),
        })
    }

    pub(crate) fn with_principal_order_quotas(
        &self,
        principal_order_quotas: Vec<PrincipalOrderQuota>,
    ) -> Self {
        let mut runtime = self.clone();
        runtime.principal_order_quotas = principal_order_quotas;
        runtime
    }

    pub(crate) fn http(&self) -> PublicHttpClient {
        self.http.clone()
    }

    pub(crate) fn async_http(&self) -> AsyncPublicHttpClient {
        self.async_http.clone()
    }

    pub(crate) fn acquire(
        &self,
        weight: u32,
        priority: RequestPriority,
    ) -> Result<(), ExchangeError> {
        let now_millis = local_unix_millis();
        self.acquire_at(weight, priority, now_millis)
    }

    pub(crate) fn observe_response(&self, response: &HttpJsonResponse) {
        let Some(used_weight) = response
            .headers
            .get("x-mbx-used-weight-1m")
            .and_then(|value| value.parse::<u32>().ok())
        else {
            return;
        };
        let now_millis = local_unix_millis();
        if let Some(shared_quota) = &self.shared_quota {
            shared_quota.observe(used_weight, now_millis);
        } else {
            self.observe_used_weight_at(used_weight, now_millis);
        }
        for principal_quota in &self.principal_order_quotas {
            if let Some(used) = response
                .headers
                .get(principal_quota.header_name)
                .and_then(|value| value.parse::<u32>().ok())
            {
                principal_quota.quota.observe(used, now_millis);
            }
        }
    }

    fn observe_used_weight_at(&self, used_weight: u32, now_millis: u64) {
        let minute = now_millis / 60_000;
        if let Ok(mut quota) = self.quota.lock() {
            if quota.minute != minute {
                quota.minute = minute;
                quota.used_weight = used_weight;
            } else {
                quota.used_weight = quota.used_weight.max(used_weight);
            }
        }
    }

    /// Return provider-adjusted time. The first signed operation calls
    /// `ensure_clock_synchronized` before reaching this method.
    pub(crate) fn now_millis(&self) -> Result<u64, ExchangeError> {
        let local = local_unix_millis();
        let clock = self
            .clock
            .lock()
            .map_err(|_| ExchangeError::Connection("Binance clock lock is poisoned".into()))?;
        Ok(apply_offset(local, clock.offset_millis))
    }

    /// Synchronize once per provider context and refresh periodically. The
    /// mutex intentionally covers the query so concurrent principals cannot
    /// stampede the provider time endpoint.
    pub(crate) fn ensure_clock_synchronized(
        &self,
        rest_base_url: &str,
    ) -> Result<ClockSnapshot, ExchangeError> {
        let mut clock = self
            .clock
            .lock()
            .map_err(|_| ExchangeError::Connection("Binance clock lock is poisoned".into()))?;
        if clock
            .synchronized_at
            .is_some_and(|instant| instant.elapsed() < CLOCK_SYNC_TTL)
        {
            return Ok(snapshot(&clock));
        }

        self.acquire(1, RequestPriority::Background)
            .map_err(|error| ExchangeError::Preflight(error.to_string()))?;
        let sent_at = local_unix_millis();
        let endpoint = format!("{}/api/v3/time", rest_base_url.trim_end_matches('/'));
        let payload = self
            .http
            .get_json(&endpoint)
            .map_err(|error| ExchangeError::Preflight(error.to_string()))?;
        let received_at = local_unix_millis();
        let server_time = payload
            .get("serverTime")
            .and_then(serde_json::Value::as_u64)
            .ok_or_else(|| {
                ExchangeError::Preflight(
                    "Binance server-time response is missing serverTime".into(),
                )
            })?;
        clock.offset_millis = calibrated_clock_offset(server_time, received_at);
        clock.round_trip_millis = received_at.saturating_sub(sent_at);
        clock.generation = clock.generation.saturating_add(1);
        clock.synchronized_at = Some(Instant::now());
        Ok(snapshot(&clock))
    }

    /// Async clock synchronization for the default provider path. The Tokio
    /// mutex serializes refreshes without holding a blocking mutex across an
    /// await point.
    pub(crate) async fn ensure_clock_synchronized_async(
        &self,
        rest_base_url: &str,
    ) -> Result<ClockSnapshot, ExchangeError> {
        if let Some(snapshot) = self.fresh_clock_snapshot()? {
            return Ok(snapshot);
        }
        let _sync = self.async_clock_sync.lock().await;
        if let Some(snapshot) = self.fresh_clock_snapshot()? {
            return Ok(snapshot);
        }
        self.acquire(1, RequestPriority::Background)
            .map_err(|error| ExchangeError::Preflight(error.to_string()))?;
        let sent_at = local_unix_millis();
        let endpoint = format!("{}/api/v3/time", rest_base_url.trim_end_matches('/'));
        let payload = self
            .async_http
            .get_json(&endpoint)
            .await
            .map_err(|error| ExchangeError::Preflight(error.to_string()))?;
        let received_at = local_unix_millis();
        let server_time = payload
            .get("serverTime")
            .and_then(serde_json::Value::as_u64)
            .ok_or_else(|| {
                ExchangeError::Preflight(
                    "Binance server-time response is missing serverTime".into(),
                )
            })?;
        let mut clock = self
            .clock
            .lock()
            .map_err(|_| ExchangeError::Connection("Binance clock lock is poisoned".into()))?;
        clock.offset_millis = calibrated_clock_offset(server_time, received_at);
        clock.round_trip_millis = received_at.saturating_sub(sent_at);
        clock.generation = clock.generation.saturating_add(1);
        clock.synchronized_at = Some(Instant::now());
        Ok(snapshot(&clock))
    }

    fn fresh_clock_snapshot(&self) -> Result<Option<ClockSnapshot>, ExchangeError> {
        let clock = self
            .clock
            .lock()
            .map_err(|_| ExchangeError::Connection("Binance clock lock is poisoned".into()))?;
        Ok(clock
            .synchronized_at
            .is_some_and(|instant| instant.elapsed() < CLOCK_SYNC_TTL)
            .then(|| snapshot(&clock)))
    }

    pub(crate) fn invalidate_clock(&self) -> Result<(), ExchangeError> {
        let mut clock = self
            .clock
            .lock()
            .map_err(|_| ExchangeError::Connection("Binance clock lock is poisoned".into()))?;
        clock.synchronized_at = None;
        Ok(())
    }

    fn acquire_at(
        &self,
        weight: u32,
        priority: RequestPriority,
        now_millis: u64,
    ) -> Result<(), ExchangeError> {
        if let Some(shared_quota) = &self.shared_quota {
            return shared_quota
                .acquire(
                    weight,
                    if priority == RequestPriority::Cancel {
                        SharedQuotaPriority::Reserved
                    } else {
                        SharedQuotaPriority::Ordinary
                    },
                    now_millis,
                )
                .map_err(|exhausted| ExchangeError::LocalRateLimit {
                    retry_after_millis: exhausted.retry_after_millis,
                    message: format!(
                        "Binance Spot shared request-weight allocation exhausted for {priority:?}"
                    ),
                });
        }
        let minute = now_millis / 60_000;
        let mut quota = self
            .quota
            .lock()
            .map_err(|_| ExchangeError::Connection("Binance quota lock is poisoned".into()))?;
        if quota.minute != minute {
            quota.minute = minute;
            quota.used_weight = 0;
        }
        let limit = if priority == RequestPriority::Cancel {
            self.allocation.request_weight_per_minute
        } else {
            self.allocation
                .request_weight_per_minute
                .saturating_sub(self.allocation.cancel_reserve_weight)
        };
        let next = quota.used_weight.saturating_add(weight);
        if next > limit {
            let retry_after_millis = (minute + 1)
                .saturating_mul(60_000)
                .saturating_sub(now_millis);
            return Err(ExchangeError::LocalRateLimit {
                retry_after_millis,
                message: format!(
                    "Binance Spot local request-weight allocation exhausted for {priority:?}"
                ),
            });
        }
        quota.used_weight = next;
        Ok(())
    }

    pub(crate) fn acquire_new_order(&self) -> Result<(), ExchangeError> {
        let now_millis = local_unix_millis();
        self.acquire_at(1, RequestPriority::NewOrder, now_millis)?;
        for principal_quota in &self.principal_order_quotas {
            principal_quota
                .quota
                .acquire(1, SharedQuotaPriority::Ordinary, now_millis)
                .map_err(|exhausted| ExchangeError::LocalRateLimit {
                    retry_after_millis: exhausted.retry_after_millis,
                    message: format!(
                        "Binance Spot shared principal order quota {} is exhausted",
                        principal_quota.header_name
                    ),
                })?;
        }
        Ok(())
    }

    #[cfg(test)]
    pub(crate) fn shares_http_worker_with(&self, other: &Self) -> bool {
        self.http.shares_worker_with(&other.http)
    }
}

fn local_unix_millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

fn signed_difference(left: u64, right: u64) -> i64 {
    if left >= right {
        left.saturating_sub(right).min(i64::MAX as u64) as i64
    } else {
        -(right.saturating_sub(left).min(i64::MAX as u64) as i64)
    }
}

fn calibrated_clock_offset(server_time: u64, received_at: u64) -> i64 {
    signed_difference(
        server_time.saturating_sub(CLOCK_SAFETY_LAG_MILLIS),
        received_at,
    )
}

fn apply_offset(value: u64, offset: i64) -> u64 {
    if offset >= 0 {
        value.saturating_add(offset as u64)
    } else {
        value.saturating_sub(offset.unsigned_abs())
    }
}

fn snapshot(clock: &ServerClock) -> ClockSnapshot {
    ClockSnapshot {
        offset_millis: clock.offset_millis,
        generation: clock.generation,
        round_trip_millis: clock.round_trip_millis,
    }
}

#[cfg(test)]
mod tests {
    use super::{
        apply_offset, calibrated_clock_offset, signed_difference, BinanceSpotProviderRuntime,
        PrincipalOrderQuota, QuotaAllocation, RequestPriority,
    };
    use crate::services::quota::SharedFixedWindowQuota;
    use crate::services::transport::http::{ExchangeError, PublicHttpClient};
    use std::sync::Arc;

    #[test]
    fn cancel_reserve_is_not_consumed_by_queries_or_new_orders() {
        let runtime = BinanceSpotProviderRuntime::new(
            PublicHttpClient::new("quota-test").unwrap(),
            QuotaAllocation {
                request_weight_per_minute: 10,
                cancel_reserve_weight: 2,
            },
        )
        .unwrap();
        runtime
            .acquire_at(8, RequestPriority::Reconciliation, 60_000)
            .unwrap();
        assert!(matches!(
            runtime.acquire_at(1, RequestPriority::NewOrder, 60_001),
            Err(ExchangeError::LocalRateLimit { .. })
        ));
        runtime
            .acquire_at(2, RequestPriority::Cancel, 60_002)
            .unwrap();
        assert!(matches!(
            runtime.acquire_at(1, RequestPriority::Cancel, 60_003),
            Err(ExchangeError::LocalRateLimit { .. })
        ));
    }

    #[test]
    fn fixed_provider_window_resets_on_next_minute() {
        let runtime = BinanceSpotProviderRuntime::new(
            PublicHttpClient::new("quota-window-test").unwrap(),
            QuotaAllocation {
                request_weight_per_minute: 2,
                cancel_reserve_weight: 0,
            },
        )
        .unwrap();
        runtime
            .acquire_at(2, RequestPriority::Background, 119_999)
            .unwrap();
        runtime
            .acquire_at(2, RequestPriority::Background, 120_000)
            .unwrap();
    }

    #[test]
    fn server_offset_handles_provider_clock_ahead_and_behind() {
        assert_eq!(signed_difference(1_250, 1_000), 250);
        assert_eq!(signed_difference(750, 1_000), -250);
        assert_eq!(apply_offset(1_000, 250), 1_250);
        assert_eq!(apply_offset(1_000, -250), 750);
        assert_eq!(calibrated_clock_offset(10_000, 10_000), -250);
    }

    #[test]
    fn provider_used_weight_calibrates_local_window_monotonically() {
        let runtime = BinanceSpotProviderRuntime::new(
            PublicHttpClient::new("quota-observation-test").unwrap(),
            QuotaAllocation {
                request_weight_per_minute: 10,
                cancel_reserve_weight: 2,
            },
        )
        .unwrap();
        runtime.observe_used_weight_at(7, 60_000);
        runtime.observe_used_weight_at(3, 60_001);
        runtime
            .acquire_at(1, RequestPriority::Reconciliation, 60_002)
            .unwrap();
        assert!(matches!(
            runtime.acquire_at(1, RequestPriority::Reconciliation, 60_003),
            Err(ExchangeError::LocalRateLimit { .. })
        ));
        runtime
            .acquire_at(2, RequestPriority::Cancel, 60_004)
            .unwrap();
    }

    #[test]
    fn new_order_consumes_shared_egress_and_principal_scopes() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("provider-quota.mmap");
        let egress = SharedFixedWindowQuota::open_or_register(
            &path,
            "binance:test:egress:primary:request-weight-1m",
            10,
            2,
            60_000,
        )
        .unwrap();
        let principal = Arc::new(
            SharedFixedWindowQuota::open_or_register(
                &path,
                "binance:test:principal:account-a:unfilled-orders-10s",
                2,
                0,
                10_000,
            )
            .unwrap(),
        );
        let runtime = BinanceSpotProviderRuntime::new_with_shared_quota(
            PublicHttpClient::new("multi-scope-quota-test").unwrap(),
            QuotaAllocation {
                request_weight_per_minute: 10,
                cancel_reserve_weight: 2,
            },
            Some(egress),
        )
        .unwrap()
        .with_principal_order_quotas(vec![PrincipalOrderQuota {
            header_name: "x-mbx-order-count-10s",
            quota: principal,
        }]);

        runtime.acquire_new_order().unwrap();
        runtime.acquire_new_order().unwrap();
        assert!(matches!(
            runtime.acquire_new_order(),
            Err(ExchangeError::LocalRateLimit { .. })
        ));
    }
}
