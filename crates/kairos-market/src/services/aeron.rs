//! Aeron consumer for Reference change notifications.

use std::collections::VecDeque;
use std::ffi::CString;
use std::sync::{Arc, Mutex};

use aeron::aeron::Aeron;
use aeron::context::Context;
use aeron::subscription::Subscription;

use crate::application::wire::{decode_reference_changed, ReferenceChangeNotice};

pub struct AeronReferenceChangeSource {
    _aeron: Aeron,
    subscription: Arc<Mutex<Subscription>>,
    queue: VecDeque<ReferenceChangeNotice>,
}

impl AeronReferenceChangeSource {
    pub fn connect(aeron_dir: Option<&str>, channel: &str, stream_id: i32) -> Result<Self, String> {
        let mut context = Context::new();
        if let Some(directory) = aeron_dir {
            context.set_aeron_dir(directory.to_string());
        }
        let mut aeron = Aeron::new(context).map_err(|error| format!("connect Aeron: {error:?}"))?;
        let channel = CString::new(channel).map_err(|error| error.to_string())?;
        let registration_id = aeron
            .add_subscription(channel, stream_id)
            .map_err(|error| format!("add Reference subscription: {error:?}"))?;
        let subscription = wait_for_subscription(&mut aeron, registration_id)?;
        Ok(Self {
            _aeron: aeron,
            subscription,
            queue: VecDeque::new(),
        })
    }

    fn poll_frames(&mut self) -> Result<(), String> {
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
                64,
            );
        for frame in frames {
            self.queue.push_back(decode_reference_changed(&frame)?);
        }
        Ok(())
    }
}

impl AeronReferenceChangeSource {
    pub fn next_change(&mut self) -> Result<Option<ReferenceChangeNotice>, String> {
        self.poll_frames()?;
        Ok(self.queue.pop_front())
    }
}

fn wait_for_subscription(
    aeron: &mut Aeron,
    registration_id: i64,
) -> Result<Arc<Mutex<Subscription>>, String> {
    for _ in 0..10_000 {
        if let Ok(subscription) = aeron.find_subscription(registration_id) {
            return Ok(subscription);
        }
        std::thread::yield_now();
    }
    Err(format!(
        "Reference Aeron subscription {registration_id} was not acknowledged"
    ))
}
