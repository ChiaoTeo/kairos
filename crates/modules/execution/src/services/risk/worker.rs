use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread::JoinHandle;
use std::time::Duration;

use kairos_primitives::decimal::Money;
use kairos_primitives::time::UnixNanos;

use super::simulated::SimulatedRiskReservations;
use super::{SimulatedRiskBehavior, SocketExecutionRiskReservations};
use crate::application::{
    RiskAuthorizationContext, RiskCommandFailure, RiskCommandResult, SubmitOrder,
};
use crate::domain::RiskReservationEvidence;

enum RiskRequest {
    Authorize(
        SubmitOrder,
        RiskAuthorizationContext,
        std::sync::mpsc::SyncSender<RiskCommandResult<RiskReservationEvidence>>,
    ),
    Reconcile(
        RiskReservationEvidence,
        std::sync::mpsc::SyncSender<Result<Option<RiskReservationEvidence>, String>>,
    ),
    Resize(
        RiskReservationEvidence,
        Money,
        UnixNanos,
        std::sync::mpsc::SyncSender<RiskCommandResult<()>>,
    ),
    Release(
        RiskReservationEvidence,
        UnixNanos,
        std::sync::mpsc::SyncSender<RiskCommandResult<()>>,
    ),
    Consume(
        RiskReservationEvidence,
        UnixNanos,
        std::sync::mpsc::SyncSender<RiskCommandResult<()>>,
    ),
}

enum RiskBackend {
    Socket(SocketExecutionRiskReservations),
    Simulated(SimulatedRiskReservations),
}

impl RiskBackend {
    fn authorize(
        &mut self,
        request: &SubmitOrder,
        context: &RiskAuthorizationContext,
    ) -> RiskCommandResult<RiskReservationEvidence> {
        match self {
            Self::Socket(service) => service.authorize(request, context),
            Self::Simulated(service) => service.authorize(request, context),
        }
    }

    fn reconcile(
        &mut self,
        evidence: &RiskReservationEvidence,
    ) -> Result<Option<RiskReservationEvidence>, String> {
        match self {
            Self::Socket(service) => service.reconcile(evidence),
            Self::Simulated(service) => service.reconcile(evidence),
        }
    }

    fn resize(
        &mut self,
        evidence: &RiskReservationEvidence,
        amount: Money,
        at: UnixNanos,
    ) -> RiskCommandResult<()> {
        match self {
            Self::Socket(service) => service.resize(evidence, amount, at),
            Self::Simulated(service) => service.resize(evidence, amount, at),
        }
    }

    fn release(
        &mut self,
        evidence: &RiskReservationEvidence,
        at: UnixNanos,
    ) -> RiskCommandResult<()> {
        match self {
            Self::Socket(service) => service.release(evidence, at),
            Self::Simulated(service) => service.release(evidence, at),
        }
    }

    fn consume(
        &mut self,
        evidence: &RiskReservationEvidence,
        at: UnixNanos,
    ) -> RiskCommandResult<()> {
        match self {
            Self::Socket(service) => service.consume(evidence, at),
            Self::Simulated(service) => service.consume(evidence, at),
        }
    }
}

/// Bounded worker boundary for all Risk command and indexed recovery I/O.
pub struct QueuedExecutionRiskReservations {
    sender: std::sync::mpsc::SyncSender<RiskRequest>,
    stop: Arc<AtomicBool>,
    worker: Option<JoinHandle<()>>,
}

impl QueuedExecutionRiskReservations {
    pub fn start(inner: SocketExecutionRiskReservations, capacity: usize) -> Result<Self, String> {
        Self::start_backend(RiskBackend::Socket(inner), capacity)
    }

    pub fn simulated(behavior: SimulatedRiskBehavior, capacity: usize) -> Result<Self, String> {
        Self::start_backend(
            RiskBackend::Simulated(SimulatedRiskReservations::new(behavior)),
            capacity,
        )
    }

    fn start_backend(inner: RiskBackend, capacity: usize) -> Result<Self, String> {
        let (sender, receiver) = std::sync::mpsc::sync_channel(capacity.max(1));
        let stop = Arc::new(AtomicBool::new(false));
        let worker_stop = Arc::clone(&stop);
        let worker = std::thread::Builder::new()
            .name("execution-risk-reservations".into())
            .spawn(move || Self::run(inner, receiver, worker_stop))
            .map_err(|error| format!("start Execution Risk worker: {error}"))?;
        Ok(Self {
            sender,
            stop,
            worker: Some(worker),
        })
    }

    fn run(
        mut inner: RiskBackend,
        receiver: std::sync::mpsc::Receiver<RiskRequest>,
        stop: Arc<AtomicBool>,
    ) {
        while !stop.load(Ordering::Acquire) {
            let request = match receiver.recv_timeout(Duration::from_millis(50)) {
                Ok(request) => request,
                Err(std::sync::mpsc::RecvTimeoutError::Timeout) => continue,
                Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
            };
            match request {
                RiskRequest::Authorize(request, context, reply) => {
                    let _ = reply.send(inner.authorize(&request, &context));
                },
                RiskRequest::Reconcile(evidence, reply) => {
                    let _ = reply.send(inner.reconcile(&evidence));
                },
                RiskRequest::Resize(evidence, amount, at, reply) => {
                    let _ = reply.send(inner.resize(&evidence, amount, at));
                },
                RiskRequest::Release(evidence, at, reply) => {
                    let _ = reply.send(inner.release(&evidence, at));
                },
                RiskRequest::Consume(evidence, at, reply) => {
                    let _ = reply.send(inner.consume(&evidence, at));
                },
            }
        }
    }

    fn send<T>(
        &self,
        request: RiskRequest,
        reply: std::sync::mpsc::Receiver<Result<T, String>>,
    ) -> Result<T, String> {
        self.sender
            .try_send(request)
            .map_err(|error| format!("Execution Risk queue unavailable: {error}"))?;
        reply
            .recv_timeout(Duration::from_secs(5))
            .map_err(|error| format!("Execution Risk worker unavailable: {error}"))?
    }

    fn send_command<T>(
        &self,
        request: RiskRequest,
        reply: std::sync::mpsc::Receiver<RiskCommandResult<T>>,
    ) -> RiskCommandResult<T> {
        self.sender.try_send(request).map_err(|error| {
            RiskCommandFailure::NotSent(format!("Execution Risk queue unavailable: {error}"))
        })?;
        reply
            .recv_timeout(Duration::from_secs(5))
            .map_err(|error| {
                RiskCommandFailure::Indeterminate(format!(
                    "Execution Risk worker response unavailable after enqueue: {error}"
                ))
            })?
    }
}

impl QueuedExecutionRiskReservations {
    pub fn authorize(
        &mut self,
        request: &SubmitOrder,
        context: &RiskAuthorizationContext,
    ) -> RiskCommandResult<RiskReservationEvidence> {
        let (tx, rx) = std::sync::mpsc::sync_channel(1);
        self.send_command(
            RiskRequest::Authorize(request.clone(), context.clone(), tx),
            rx,
        )
    }

    pub fn reconcile(
        &mut self,
        evidence: &RiskReservationEvidence,
    ) -> Result<Option<RiskReservationEvidence>, String> {
        let (tx, rx) = std::sync::mpsc::sync_channel(1);
        self.send(RiskRequest::Reconcile(evidence.clone(), tx), rx)
    }

    pub fn resize(
        &mut self,
        evidence: &RiskReservationEvidence,
        amount: Money,
        at: UnixNanos,
    ) -> RiskCommandResult<()> {
        let (tx, rx) = std::sync::mpsc::sync_channel(1);
        self.send_command(RiskRequest::Resize(evidence.clone(), amount, at, tx), rx)
    }

    pub fn release(
        &mut self,
        evidence: &RiskReservationEvidence,
        at: UnixNanos,
    ) -> RiskCommandResult<()> {
        let (tx, rx) = std::sync::mpsc::sync_channel(1);
        self.send_command(RiskRequest::Release(evidence.clone(), at, tx), rx)
    }

    pub fn consume(
        &mut self,
        evidence: &RiskReservationEvidence,
        at: UnixNanos,
    ) -> RiskCommandResult<()> {
        let (tx, rx) = std::sync::mpsc::sync_channel(1);
        self.send_command(RiskRequest::Consume(evidence.clone(), at, tx), rx)
    }
}

impl Drop for QueuedExecutionRiskReservations {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}
