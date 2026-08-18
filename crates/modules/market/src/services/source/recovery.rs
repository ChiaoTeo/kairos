//! Reconnect policy for live provider streams.

use std::collections::VecDeque;
use std::time::Duration;

use kairos_integration::{ConnectionLifecycleCommand, IntegrationError};
use tokio::sync::mpsc;

use super::messages::{SourceCommand, SourceInput};
use super::stream::{fail, failure_kind, status};
use crate::domain::source::{SourceEpoch, SourceFailureKind, SourceId, SourceStatus};

pub(super) fn reconnectable(error: &IntegrationError) -> bool {
    matches!(
        error,
        IntegrationError::NotReady
            | IntegrationError::RateLimited(_)
            | IntegrationError::Transport(_)
            | IntegrationError::Backpressure(_)
            | IntegrationError::Unavailable(_)
    )
}

pub(super) async fn recover_connection<C: ConnectionLifecycleCommand>(
    connection: &mut C,
    commands: &mut mpsc::Receiver<SourceCommand>,
    deferred_commands: &mut VecDeque<SourceCommand>,
    inputs: &mpsc::Sender<SourceInput>,
    source_id: &SourceId,
    epoch: &mut SourceEpoch,
    mut kind: SourceFailureKind,
    mut reason: String,
) -> bool {
    let mut delay = Duration::from_millis(250);
    loop {
        fail(inputs, source_id, *epoch, kind, reason.clone()).await;
        if status(inputs, source_id, *epoch, SourceStatus::Reconnecting)
            .await
            .is_err()
        {
            return false;
        }
        tokio::select! {
            _ = tokio::time::sleep(delay) => {}
            command = commands.recv() => match command {
                Some(SourceCommand::Shutdown) | None => {
                    let _ = connection.disconnect().await;
                    let _ = status(inputs, source_id, *epoch, SourceStatus::Stopped).await;
                    return false;
                }
                Some(command) => {
                    deferred_commands.push_back(command);
                    continue;
                }
            }
        }
        match connection.reconnect().await {
            Ok(()) => {
                epoch.advance();
                return status(inputs, source_id, *epoch, SourceStatus::Ready)
                    .await
                    .is_ok();
            }
            Err(error) if reconnectable(&error) => {
                kind = failure_kind(&error);
                reason = error.to_string();
                delay = delay.saturating_mul(2).min(Duration::from_secs(5));
            }
            Err(error) => {
                fail(
                    inputs,
                    source_id,
                    *epoch,
                    failure_kind(&error),
                    error.to_string(),
                )
                .await;
                return false;
            }
        }
    }
}
