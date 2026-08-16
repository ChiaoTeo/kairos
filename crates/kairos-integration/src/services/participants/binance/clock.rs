//! Binance server-clock synchronization, kept separate from request quota and transport.

use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use crate::services::participants::binance::spot::runtime::{
    BinanceRequestRuntime, RequestPriority,
};
use crate::services::transport::http::ExchangeError;

const CLOCK_SYNC_TTL: Duration = Duration::from_secs(15 * 60);
// Anchor the returned value to receive time and stay slightly behind Binance.
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
pub(crate) struct BinanceServerClock {
    state: Arc<Mutex<ServerClock>>,
    async_sync: Arc<tokio::sync::Mutex<()>>,
}

impl Default for BinanceServerClock {
    fn default() -> Self {
        Self {
            state: Arc::new(Mutex::new(ServerClock::default())),
            async_sync: Arc::new(tokio::sync::Mutex::new(())),
        }
    }
}

impl BinanceServerClock {
    pub(crate) fn now_millis(&self) -> Result<u64, ExchangeError> {
        let local = local_unix_millis();
        let clock = self
            .state
            .lock()
            .map_err(|_| ExchangeError::Connection("Binance clock lock is poisoned".into()))?;
        Ok(apply_offset(local, clock.offset_millis))
    }

    pub(crate) fn ensure_synchronized(
        &self,
        runtime: &BinanceRequestRuntime,
        rest_base_url: &str,
        time_path: &str,
    ) -> Result<ClockSnapshot, ExchangeError> {
        let mut clock = self
            .state
            .lock()
            .map_err(|_| ExchangeError::Connection("Binance clock lock is poisoned".into()))?;
        if clock
            .synchronized_at
            .is_some_and(|instant| instant.elapsed() < CLOCK_SYNC_TTL)
        {
            return Ok(snapshot(&clock));
        }
        runtime
            .acquire(1, RequestPriority::Background)
            .map_err(|error| ExchangeError::Preflight(error.to_string()))?;
        let sent_at = local_unix_millis();
        let endpoint = format!("{}{}", rest_base_url.trim_end_matches('/'), time_path);
        let payload = runtime
            .http()
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
        update_clock(&mut clock, server_time, sent_at, received_at);
        Ok(snapshot(&clock))
    }

    pub(crate) async fn ensure_synchronized_async(
        &self,
        runtime: &BinanceRequestRuntime,
        rest_base_url: &str,
        time_path: &str,
    ) -> Result<ClockSnapshot, ExchangeError> {
        if let Some(snapshot) = self.fresh_snapshot()? {
            return Ok(snapshot);
        }
        let _sync = self.async_sync.lock().await;
        if let Some(snapshot) = self.fresh_snapshot()? {
            return Ok(snapshot);
        }
        runtime
            .acquire(1, RequestPriority::Background)
            .map_err(|error| ExchangeError::Preflight(error.to_string()))?;
        let sent_at = local_unix_millis();
        let endpoint = format!("{}{}", rest_base_url.trim_end_matches('/'), time_path);
        let payload = runtime
            .async_http()
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
            .state
            .lock()
            .map_err(|_| ExchangeError::Connection("Binance clock lock is poisoned".into()))?;
        update_clock(&mut clock, server_time, sent_at, received_at);
        Ok(snapshot(&clock))
    }

    pub(crate) fn invalidate(&self) -> Result<(), ExchangeError> {
        let mut clock = self
            .state
            .lock()
            .map_err(|_| ExchangeError::Connection("Binance clock lock is poisoned".into()))?;
        clock.synchronized_at = None;
        Ok(())
    }

    fn fresh_snapshot(&self) -> Result<Option<ClockSnapshot>, ExchangeError> {
        let clock = self
            .state
            .lock()
            .map_err(|_| ExchangeError::Connection("Binance clock lock is poisoned".into()))?;
        Ok(clock
            .synchronized_at
            .is_some_and(|instant| instant.elapsed() < CLOCK_SYNC_TTL)
            .then(|| snapshot(&clock)))
    }
}

fn update_clock(clock: &mut ServerClock, server_time: u64, sent_at: u64, received_at: u64) {
    clock.offset_millis = calibrated_clock_offset(server_time, received_at);
    clock.round_trip_millis = received_at.saturating_sub(sent_at);
    clock.generation = clock.generation.saturating_add(1);
    clock.synchronized_at = Some(Instant::now());
}

fn snapshot(clock: &ServerClock) -> ClockSnapshot {
    ClockSnapshot {
        offset_millis: clock.offset_millis,
        generation: clock.generation,
        round_trip_millis: clock.round_trip_millis,
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

#[cfg(test)]
mod tests {
    use super::{apply_offset, calibrated_clock_offset, signed_difference};

    #[test]
    fn server_offset_handles_provider_clock_ahead_and_behind() {
        assert_eq!(signed_difference(1_250, 1_000), 250);
        assert_eq!(signed_difference(750, 1_000), -250);
        assert_eq!(apply_offset(1_000, 250), 1_250);
        assert_eq!(apply_offset(1_000, -250), 750);
        assert_eq!(calibrated_clock_offset(10_000, 10_000), -250);
    }
}
