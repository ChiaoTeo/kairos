//! Byte-level Aeron transport primitives.
//!
//! This module owns Aeron connection, publication back-pressure, and frame
//! extraction. It deliberately does not know any Kairos business schema.
//!
//! The client is provided by `rusteron-client`, which tracks the Aeron C ABI
//! and CnC layout used by the current Media Driver. Keeping that pairing in
//! one dependency avoids the silent layout mismatch of the old `aeron 0.2`
//! Rust port.

use rusteron_client::{Aeron, AeronContext, AeronPublication, AeronSubscription};
use std::collections::VecDeque;
use std::ffi::CString;
use std::sync::{Arc, Mutex};
use std::time::Duration;

// Reference catalogs containing a useful option chain can exceed 4 MiB after
// FlatBuffers encoding. Keep one transport default large enough for a full
// snapshot while retaining the explicit capacity constructor for tighter
// consumers.
const DEFAULT_BUFFER_CAPACITY: usize = 16 * 1024 * 1024;
const DEFAULT_RETRY_LIMIT: usize = 10_000;
const MEDIA_DRIVER_TIMEOUT: Duration = Duration::from_secs(10);

pub struct AeronBytePublisher {
    _aeron: Aeron,
    publication: Arc<Mutex<AeronPublication>>,
    buffer_capacity: usize,
    retry_limit: usize,
}

impl AeronBytePublisher {
    pub fn connect(aeron_dir: Option<&str>, channel: &str, stream_id: i32) -> Result<Self, String> {
        Self::connect_with_capacity(aeron_dir, channel, stream_id, DEFAULT_BUFFER_CAPACITY)
    }

    pub fn connect_with_capacity(
        aeron_dir: Option<&str>,
        channel: &str,
        stream_id: i32,
        buffer_capacity: usize,
    ) -> Result<Self, String> {
        if buffer_capacity == 0 {
            return Err("Aeron buffer capacity must be positive".into());
        }
        let aeron = connect_client(aeron_dir)?;
        let channel = CString::new(channel).map_err(|error| error.to_string())?;
        let publication = aeron
            .add_publication(&channel, stream_id, MEDIA_DRIVER_TIMEOUT)
            .map_err(|error| format!("add Aeron publication: {error:?}"))?;
        Ok(Self {
            _aeron: aeron,
            publication: Arc::new(Mutex::new(publication)),
            buffer_capacity,
            retry_limit: DEFAULT_RETRY_LIMIT,
        })
    }

    pub fn publish(&self, bytes: &[u8]) -> Result<(), String> {
        if bytes.is_empty() {
            return Err("Aeron payload must not be empty".into());
        }
        if bytes.len() > self.buffer_capacity {
            return Err(format!(
                "Aeron payload size {} exceeds buffer capacity {}",
                bytes.len(),
                self.buffer_capacity
            ));
        }
        // Reference can start before Market (or another consumer) has opened
        // its subscriptions. Keep the catalog durable in Reference's store;
        // the next refresh will publish it once a subscriber is connected.
        if !self
            .publication
            .lock()
            .map_err(|_| "Aeron publication mutex poisoned".to_string())?
            .is_connected()
        {
            return Ok(());
        }
        for _ in 0..self.retry_limit {
            let result = self
                .publication
                .lock()
                .map_err(|_| "Aeron publication mutex poisoned".to_string())?
                .offer(bytes);
            match result {
                Ok(_) => return Ok(()),
                Err(rusteron_client::AeronOfferError::NotConnected) => return Ok(()),
                Err(error) if error.is_retryable() => std::thread::yield_now(),
                Err(error) => return Err(format!("Aeron publication offer: {error:?}")),
            }
        }
        Err("Aeron publication remained back-pressured".into())
    }
}

pub struct AeronByteSubscription {
    _aeron: Aeron,
    subscription: Arc<Mutex<AeronSubscription>>,
    queue: VecDeque<Vec<u8>>,
}

impl AeronByteSubscription {
    pub fn connect(aeron_dir: Option<&str>, channel: &str, stream_id: i32) -> Result<Self, String> {
        let aeron = connect_client(aeron_dir)?;
        let channel = CString::new(channel).map_err(|error| error.to_string())?;
        let subscription = aeron
            .add_subscription(
                &channel,
                stream_id,
                rusteron_client::Handlers::NONE,
                rusteron_client::Handlers::NONE,
                MEDIA_DRIVER_TIMEOUT,
            )
            .map_err(|error| format!("add Aeron subscription: {error:?}"))?;
        Ok(Self {
            _aeron: aeron,
            subscription: Arc::new(Mutex::new(subscription)),
            queue: VecDeque::new(),
        })
    }

    pub fn next(&mut self) -> Result<Option<Vec<u8>>, String> {
        self.poll(64)?;
        Ok(self.queue.pop_front())
    }

    pub fn poll(&mut self, fragment_limit: i32) -> Result<usize, String> {
        if fragment_limit <= 0 {
            return Err("Aeron fragment limit must be positive".into());
        }
        let mut frames = Vec::new();
        let count = self
            .subscription
            .lock()
            .map_err(|_| "Aeron subscription mutex poisoned".to_string())?
            .poll_fn(
                |buffer, _header| frames.push(buffer.to_vec()),
                fragment_limit as usize,
            )
            .map_err(|error| format!("poll Aeron subscription: {error:?}"))?;
        self.queue.extend(frames);
        Ok(count as usize)
    }
}

fn connect_client(aeron_dir: Option<&str>) -> Result<Aeron, String> {
    let context =
        AeronContext::new().map_err(|error| format!("create Aeron context: {error:?}"))?;
    if let Some(directory) = aeron_dir {
        let directory = CString::new(directory).map_err(|error| error.to_string())?;
        context
            .set_dir(&directory)
            .map_err(|error| format!("set Aeron directory: {error:?}"))?;
    }
    let aeron = Aeron::new(&context).map_err(|error| format!("connect Aeron: {error:?}"))?;
    aeron
        .start()
        .map_err(|error| format!("start Aeron client: {error:?}"))?;
    Ok(aeron)
}
