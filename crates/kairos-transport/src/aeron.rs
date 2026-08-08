//! Byte-level Aeron transport primitives.
//!
//! This module owns Aeron connection, publication back-pressure, and frame
//! extraction. It deliberately does not know any Kairos business schema.

use aeron::aeron::Aeron;
use aeron::concurrent::atomic_buffer::{AlignedBuffer, AtomicBuffer};
use aeron::context::Context;
use aeron::publication::Publication;
use aeron::subscription::Subscription;
use std::collections::VecDeque;
use std::ffi::CString;
use std::sync::{Arc, Mutex};

const DEFAULT_BUFFER_CAPACITY: usize = 4 * 1024 * 1024;
const DEFAULT_RETRY_LIMIT: usize = 10_000;

pub struct AeronBytePublisher {
    _aeron: Aeron,
    publication: Arc<Mutex<Publication>>,
    buffer: AlignedBuffer,
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
        let buffer_capacity_i32 = i32::try_from(buffer_capacity)
            .map_err(|_| "Aeron buffer capacity exceeds i32::MAX".to_string())?;
        let mut context = Context::new();
        if let Some(directory) = aeron_dir {
            context.set_aeron_dir(directory.to_owned());
        }
        let mut aeron = Aeron::new(context).map_err(|error| format!("connect Aeron: {error:?}"))?;
        let channel = CString::new(channel).map_err(|error| error.to_string())?;
        let registration_id = aeron
            .add_publication(channel, stream_id)
            .map_err(|error| format!("add Aeron publication: {error:?}"))?;
        let publication = wait_for_publication(&mut aeron, registration_id)?;
        Ok(Self {
            _aeron: aeron,
            publication,
            buffer: AlignedBuffer::with_capacity(buffer_capacity_i32),
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
        let atomic = AtomicBuffer::from_aligned(&self.buffer);
        atomic.put_bytes(0, bytes);
        for _ in 0..self.retry_limit {
            match self
                .publication
                .lock()
                .map_err(|_| "Aeron publication mutex poisoned".to_string())?
                .offer_part(atomic, 0, bytes.len() as i32)
            {
                Ok(_) => return Ok(()),
                Err(_) => std::thread::yield_now(),
            }
        }
        Err("Aeron publication remained back-pressured".into())
    }
}

pub struct AeronByteSubscription {
    _aeron: Aeron,
    subscription: Arc<Mutex<Subscription>>,
    queue: VecDeque<Vec<u8>>,
}

impl AeronByteSubscription {
    pub fn connect(aeron_dir: Option<&str>, channel: &str, stream_id: i32) -> Result<Self, String> {
        let mut context = Context::new();
        if let Some(directory) = aeron_dir {
            context.set_aeron_dir(directory.to_owned());
        }
        let mut aeron = Aeron::new(context).map_err(|error| format!("connect Aeron: {error:?}"))?;
        let channel = CString::new(channel).map_err(|error| error.to_string())?;
        let registration_id = aeron
            .add_subscription(channel, stream_id)
            .map_err(|error| format!("add Aeron subscription: {error:?}"))?;
        let subscription = wait_for_subscription(&mut aeron, registration_id)?;
        Ok(Self {
            _aeron: aeron,
            subscription,
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
        self.subscription
            .lock()
            .map_err(|_| "Aeron subscription mutex poisoned".to_string())?
            .poll(
                &mut |buffer, offset, length, _header| {
                    let mut payload = vec![0_u8; length as usize];
                    buffer.get_bytes(offset, payload.as_mut_ptr(), length);
                    frames.push(payload);
                },
                fragment_limit,
            );
        let count = frames.len();
        self.queue.extend(frames);
        Ok(count)
    }
}

fn wait_for_publication(
    aeron: &mut Aeron,
    registration_id: i64,
) -> Result<Arc<Mutex<Publication>>, String> {
    for _ in 0..DEFAULT_RETRY_LIMIT {
        if let Ok(publication) = aeron.find_publication(registration_id) {
            return Ok(publication);
        }
        std::thread::yield_now();
    }
    Err(format!(
        "Aeron publication {registration_id} was not acknowledged"
    ))
}

fn wait_for_subscription(
    aeron: &mut Aeron,
    registration_id: i64,
) -> Result<Arc<Mutex<Subscription>>, String> {
    for _ in 0..DEFAULT_RETRY_LIMIT {
        if let Ok(subscription) = aeron.find_subscription(registration_id) {
            return Ok(subscription);
        }
        std::thread::yield_now();
    }
    Err(format!(
        "Aeron subscription {registration_id} was not acknowledged"
    ))
}
