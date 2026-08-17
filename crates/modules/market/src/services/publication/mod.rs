//! Private bounded Market publication queues and local fan-out.
mod fanout;
mod queue;
pub(crate) use fanout::EventFanout;
pub(crate) use queue::{EventPublication, HistoryQueue};
pub(crate) type MarketEventEncoder = fn(
    &str,
    &kairos_protocol::InstanceIdentity,
    u64,
    &crate::domain::events::MarketEvent,
) -> Result<Vec<u8>, String>;

#[cfg(test)]
mod tests {
    use super::{EventFanout, EventPublication};
    use kairos_protocol::InstanceIdentity;
    use std::collections::VecDeque;
    use std::time::Duration;
    use tokio::sync::mpsc;

    fn identity() -> InstanceIdentity {
        InstanceIdentity::new("workspace", "launch", "instance")
    }

    #[tokio::test]
    async fn publication_backlog_flushes_after_queue_capacity_returns() {
        let (sender, mut receiver) = mpsc::channel(1);
        let mut publication =
            EventPublication::new("market", identity(), sender, |_, _, _, _| Ok(Vec::new()));
        publication.pending = VecDeque::from([vec![1], vec![2]]);

        publication.flush().unwrap();
        assert_eq!(receiver.recv().await.unwrap(), vec![1]);
        assert_eq!(publication.pending.len(), 1);
        publication.drain(Duration::from_secs(1)).await.unwrap();
        assert_eq!(receiver.recv().await.unwrap(), vec![2]);
        assert!(publication.is_empty());
    }

    #[tokio::test]
    async fn slow_client_is_removed_without_blocking_healthy_client() {
        let (slow_sender, _slow_receiver) = mpsc::channel(1);
        let (healthy_sender, mut healthy_receiver) = mpsc::channel(2);
        let mut fanout = EventFanout::new(1);
        fanout.clients = vec![slow_sender, healthy_sender];

        fanout.publish(vec![1]);
        fanout.publish(vec![2]);

        assert_eq!(fanout.clients.len(), 1);
        assert_eq!(healthy_receiver.recv().await.unwrap(), vec![1]);
        assert_eq!(healthy_receiver.recv().await.unwrap(), vec![2]);
    }
}
