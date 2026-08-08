//! Aeron consumer for Reference change notifications.

use kairos_transport::AeronByteSubscription;
use std::collections::VecDeque;

use crate::application::wire::{decode_reference_changed, ReferenceChangeNotice};

pub struct AeronReferenceChangeSource {
    subscription: AeronByteSubscription,
    queue: VecDeque<ReferenceChangeNotice>,
}

impl AeronReferenceChangeSource {
    pub fn connect(aeron_dir: Option<&str>, channel: &str, stream_id: i32) -> Result<Self, String> {
        let subscription = AeronByteSubscription::connect(aeron_dir, channel, stream_id)?;
        Ok(Self {
            subscription,
            queue: VecDeque::new(),
        })
    }

    fn poll_frames(&mut self) -> Result<(), String> {
        while let Some(frame) = self.subscription.next()? {
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
