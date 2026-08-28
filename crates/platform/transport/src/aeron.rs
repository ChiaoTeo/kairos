//! Byte-level Aeron transport primitives.
//!
//! This module owns Aeron connection, publication back-pressure, and frame
//! extraction. It deliberately does not know any Kairos business schema.
//!
//! The client is provided by `rusteron-client`, which tracks the Aeron C ABI
//! and CnC layout used by the current Media Driver. Keeping that pairing in
//! one dependency avoids the silent layout mismatch of the old `aeron 0.2`
//! Rust port.

use std::collections::VecDeque;
use std::ffi::CString;
use std::panic::{AssertUnwindSafe, catch_unwind, resume_unwind};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use rusteron_client::{
    Aeron, AeronContext, AeronFragmentClosureAssembler, AeronHeader, AeronPublication,
    AeronSubscription,
};

const DEFAULT_PUBLISH_DEADLINE: Duration = Duration::from_millis(100);
const MEDIA_DRIVER_TIMEOUT: Duration = Duration::from_secs(10);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PublishOutcome {
    Offered,
    DroppedNoSubscriber,
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum AeronTransportError {
    #[error("invalid Aeron configuration: {0}")]
    Configuration(String),
    #[error("Aeron Media Driver is unavailable: {0}")]
    DriverUnavailable(String),
    #[error("Aeron operation timed out: {0}")]
    Timeout(String),
    #[error("payload size {actual} exceeds limit {limit}")]
    PayloadTooLarge { limit: usize, actual: usize },
    #[error("Aeron publication remained back-pressured until its deadline")]
    Backpressured,
    #[error("Aeron resource is closed or poisoned: {0}")]
    Closed(String),
    #[error("Aeron operation failed: {0}")]
    Operation(String),
}

/// Complete address of one Aeron stream.
///
/// Publishers and subscribers receive the same value object so a module
/// Contract cannot accidentally pair different driver directories, channels,
/// or stream identifiers.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AeronEndpoint {
    directory: Option<PathBuf>,
    channel: String,
    stream_id: i32,
}

impl AeronEndpoint {
    pub fn new(
        directory: Option<PathBuf>,
        channel: impl Into<String>,
        stream_id: i32,
    ) -> Result<Self, AeronTransportError> {
        let channel = channel.into();
        if channel.trim().is_empty() {
            return Err(AeronTransportError::Configuration(
                "Aeron channel must not be empty".into(),
            ));
        }
        if channel.as_bytes().contains(&0) {
            return Err(AeronTransportError::Configuration(
                "Aeron channel must not contain NUL".into(),
            ));
        }
        if stream_id <= 0 {
            return Err(AeronTransportError::Configuration(
                "Aeron stream id must be positive".into(),
            ));
        }
        if directory
            .as_deref()
            .is_some_and(|value| value.as_os_str().is_empty())
        {
            return Err(AeronTransportError::Configuration(
                "Aeron directory must not be empty".into(),
            ));
        }
        Ok(Self {
            directory,
            channel,
            stream_id,
        })
    }

    pub fn from_parts(
        directory: Option<&str>,
        channel: impl Into<String>,
        stream_id: i32,
    ) -> Result<Self, AeronTransportError> {
        Self::new(directory.map(PathBuf::from), channel, stream_id)
    }

    pub fn directory(&self) -> Option<&Path> {
        self.directory.as_deref()
    }

    pub fn channel(&self) -> &str {
        &self.channel
    }

    pub const fn stream_id(&self) -> i32 {
        self.stream_id
    }

    fn directory_str(&self) -> Result<Option<&str>, AeronTransportError> {
        self.directory
            .as_deref()
            .map(|value| {
                value.to_str().ok_or_else(|| {
                    AeronTransportError::Configuration("Aeron directory must be valid UTF-8".into())
                })
            })
            .transpose()
    }
}

pub struct AeronBytePublisher {
    _aeron: Aeron,
    publication: Arc<Mutex<AeronPublication>>,
    buffer_capacity: usize,
    publish_deadline: Duration,
}

impl AeronBytePublisher {
    pub fn connect_endpoint(endpoint: &AeronEndpoint) -> Result<Self, AeronTransportError> {
        Self::connect(
            endpoint.directory_str()?,
            endpoint.channel(),
            endpoint.stream_id(),
        )
    }

    pub fn connect(
        aeron_dir: Option<&str>,
        channel: &str,
        stream_id: i32,
    ) -> Result<Self, AeronTransportError> {
        Self::connect_with_capacity(
            aeron_dir,
            channel,
            stream_id,
            crate::DEFAULT_MAX_PAYLOAD_LEN,
        )
    }

    pub fn connect_with_capacity(
        aeron_dir: Option<&str>,
        channel: &str,
        stream_id: i32,
        buffer_capacity: usize,
    ) -> Result<Self, AeronTransportError> {
        if buffer_capacity == 0 {
            return Err(AeronTransportError::Configuration(
                "buffer capacity must be positive".into(),
            ));
        }
        let aeron = connect_client(aeron_dir)?;
        let channel = CString::new(channel)
            .map_err(|error| AeronTransportError::Configuration(error.to_string()))?;
        let publication = aeron
            .add_publication(&channel, stream_id, MEDIA_DRIVER_TIMEOUT)
            .map_err(|error| {
                AeronTransportError::DriverUnavailable(format!("add publication: {error:?}"))
            })?;
        Ok(Self {
            _aeron: aeron,
            publication: Arc::new(Mutex::new(publication)),
            buffer_capacity,
            publish_deadline: DEFAULT_PUBLISH_DEADLINE,
        })
    }

    pub fn publish(&self, bytes: &[u8]) -> Result<PublishOutcome, AeronTransportError> {
        if bytes.is_empty() {
            return Err(AeronTransportError::Configuration(
                "payload must not be empty".into(),
            ));
        }
        if bytes.len() > self.buffer_capacity {
            return Err(AeronTransportError::PayloadTooLarge {
                limit: self.buffer_capacity,
                actual: bytes.len(),
            });
        }
        // Aeron publications are best-effort streams.  Having no subscriber
        // is a normal lifecycle state (for example while a consumer is
        // restarting), so there is nothing to offer and nothing to retry.
        // Event recovery, when the owning contract supports it, is an
        // event-log/retention concern. A state snapshot is never an event
        // backlog and must not be used to repair an event-stream gap.
        if !self
            .publication
            .lock()
            .map_err(|_| AeronTransportError::Closed("publication mutex poisoned".into()))?
            .is_connected()
        {
            return Ok(PublishOutcome::DroppedNoSubscriber);
        }
        let deadline = Instant::now() + self.publish_deadline;
        loop {
            let result = self
                .publication
                .lock()
                .map_err(|_| AeronTransportError::Closed("publication mutex poisoned".into()))?
                .offer(bytes);
            match result {
                Ok(_) => return Ok(PublishOutcome::Offered),
                // The subscriber may disappear between is_connected() and
                // offer().  Treat that race exactly like the preflight case:
                // the realtime notification is intentionally dropped.
                Err(rusteron_client::AeronOfferError::NotConnected) => {
                    return Ok(PublishOutcome::DroppedNoSubscriber);
                },
                Err(error) if error.is_retryable() && Instant::now() < deadline => {
                    std::thread::yield_now()
                },
                Err(error) if error.is_retryable() => {
                    return Err(AeronTransportError::Backpressured);
                },
                Err(error) => {
                    return Err(AeronTransportError::Operation(format!(
                        "publication offer: {error:?}"
                    )));
                },
            }
        }
    }

    /// Whether the publication currently has at least one subscriber.
    ///
    /// Callers may use this as a best-effort readiness signal when delivery
    /// is optional. A successful `true` does not replace handling a later
    /// publish failure because the subscriber can disconnect at any time.
    pub fn has_subscriber(&self) -> Result<bool, AeronTransportError> {
        self.publication
            .lock()
            .map_err(|_| AeronTransportError::Closed("publication mutex poisoned".into()))
            .map(|publication| publication.is_connected())
    }
}

pub struct AeronByteSubscription {
    _aeron: Aeron,
    subscription: Arc<Mutex<AeronSubscription>>,
    assembler: AeronFragmentClosureAssembler,
    queue: VecDeque<Vec<u8>>,
    max_payload_len: usize,
}

impl AeronByteSubscription {
    pub fn connect_endpoint(endpoint: &AeronEndpoint) -> Result<Self, AeronTransportError> {
        Self::connect(
            endpoint.directory_str()?,
            endpoint.channel(),
            endpoint.stream_id(),
        )
    }

    pub fn connect(
        aeron_dir: Option<&str>,
        channel: &str,
        stream_id: i32,
    ) -> Result<Self, AeronTransportError> {
        Self::connect_with_capacity(
            aeron_dir,
            channel,
            stream_id,
            crate::DEFAULT_MAX_PAYLOAD_LEN,
        )
    }

    pub fn connect_with_capacity(
        aeron_dir: Option<&str>,
        channel: &str,
        stream_id: i32,
        max_payload_len: usize,
    ) -> Result<Self, AeronTransportError> {
        if max_payload_len == 0 || max_payload_len > u32::MAX as usize {
            return Err(AeronTransportError::Configuration(
                "max payload length must be in the u32 framing range".into(),
            ));
        }
        let aeron = connect_client(aeron_dir)?;
        let channel = CString::new(channel)
            .map_err(|error| AeronTransportError::Configuration(error.to_string()))?;
        let subscription = aeron
            .add_subscription(
                &channel,
                stream_id,
                rusteron_client::Handlers::NONE,
                rusteron_client::Handlers::NONE,
                MEDIA_DRIVER_TIMEOUT,
            )
            .map_err(|error| {
                AeronTransportError::DriverUnavailable(format!("add subscription: {error:?}"))
            })?;
        Ok(Self {
            _aeron: aeron,
            subscription: Arc::new(Mutex::new(subscription)),
            assembler: AeronFragmentClosureAssembler::new().map_err(|error| {
                AeronTransportError::Operation(format!("create fragment assembler: {error:?}"))
            })?,
            queue: VecDeque::new(),
            max_payload_len,
        })
    }

    pub fn next_frame(&mut self) -> Result<Option<Vec<u8>>, AeronTransportError> {
        self.poll(64)?;
        Ok(self.queue.pop_front())
    }

    pub fn poll(&mut self, fragment_limit: i32) -> Result<usize, AeronTransportError> {
        let mut frames = Vec::new();
        let count = self.poll_with(fragment_limit, |buffer| frames.push(buffer.to_vec()))?;
        self.queue.extend(frames);
        Ok(count)
    }

    /// Poll complete Aeron messages and visit each message without copying it.
    ///
    /// The borrowed slice is valid only for the duration of the callback. It
    /// may refer directly to an Aeron term buffer for an unfragmented message,
    /// or to the fragment assembler's reusable buffer for a fragmented one.
    /// Callers must finish decoding and handling the message before returning;
    /// retaining a pointer to the slice beyond the callback is invalid.
    ///
    /// Unlike [`Self::poll`], this method never appends messages to the owned
    /// frame queue. The returned count is Aeron's fragment count, which can be
    /// greater than the number of complete messages delivered to `visitor`.
    pub fn poll_with<F>(
        &mut self,
        fragment_limit: i32,
        mut visitor: F,
    ) -> Result<usize, AeronTransportError>
    where
        F: FnMut(&[u8]),
    {
        if fragment_limit <= 0 {
            return Err(AeronTransportError::Configuration(
                "fragment limit must be positive".into(),
            ));
        }

        let mut context = BorrowedFrameContext {
            visitor: &mut visitor,
            max_payload_len: self.max_payload_len,
            oversized: None,
            panic: None,
        };
        let subscription = self
            .subscription
            .lock()
            .map_err(|_| AeronTransportError::Closed("subscription mutex poisoned".into()))?;
        let count = self
            .assembler
            .poll(
                &*subscription,
                &mut context,
                visit_borrowed_frame::<F>,
                fragment_limit as usize,
            )
            .map_err(|error| {
                AeronTransportError::Operation(format!("poll subscription: {error:?}"))
            })?;

        if let Some(panic) = context.panic {
            resume_unwind(panic);
        }
        if let Some(actual) = context.oversized {
            return Err(AeronTransportError::PayloadTooLarge {
                limit: self.max_payload_len,
                actual,
            });
        }
        Ok(count as usize)
    }
}

struct BorrowedFrameContext<'a, F> {
    visitor: &'a mut F,
    max_payload_len: usize,
    oversized: Option<usize>,
    panic: Option<Box<dyn std::any::Any + Send>>,
}

fn visit_borrowed_frame<F>(
    context: &mut BorrowedFrameContext<'_, F>,
    buffer: &[u8],
    _header: AeronHeader,
) where
    F: FnMut(&[u8]),
{
    if context.oversized.is_some() || context.panic.is_some() {
        return;
    }
    if buffer.len() > context.max_payload_len {
        context.oversized = Some(buffer.len());
        return;
    }
    if let Err(panic) = catch_unwind(AssertUnwindSafe(|| (context.visitor)(buffer))) {
        context.panic = Some(panic);
    }
}

fn connect_client(aeron_dir: Option<&str>) -> Result<Aeron, AeronTransportError> {
    let context = AeronContext::new().map_err(|error| {
        AeronTransportError::DriverUnavailable(format!("create context: {error:?}"))
    })?;
    if let Some(directory) = aeron_dir {
        let directory = CString::new(directory)
            .map_err(|error| AeronTransportError::Configuration(error.to_string()))?;
        context.set_dir(&directory).map_err(|error| {
            AeronTransportError::Configuration(format!("set directory: {error:?}"))
        })?;
    }
    let aeron = Aeron::new(&context)
        .map_err(|error| AeronTransportError::DriverUnavailable(format!("connect: {error:?}")))?;
    aeron.start().map_err(|error| {
        AeronTransportError::DriverUnavailable(format!("start client: {error:?}"))
    })?;
    Ok(aeron)
}

#[cfg(test)]
mod tests {
    use std::path::Path;

    use super::AeronEndpoint;

    #[test]
    fn endpoint_keeps_one_complete_stream_address() {
        let endpoint =
            AeronEndpoint::from_parts(Some("/tmp/kairos-aeron"), "aeron:ipc", 1_301).unwrap();
        assert_eq!(endpoint.directory(), Some(Path::new("/tmp/kairos-aeron")));
        assert_eq!(endpoint.channel(), "aeron:ipc");
        assert_eq!(endpoint.stream_id(), 1_301);
    }

    #[test]
    fn endpoint_rejects_incomplete_addresses() {
        assert!(AeronEndpoint::from_parts(None, "", 1).is_err());
        assert!(AeronEndpoint::from_parts(None, "aeron:ipc", 0).is_err());
        assert!(AeronEndpoint::from_parts(Some(""), "aeron:ipc", 1).is_err());
    }
}
