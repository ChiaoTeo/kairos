//! Focused bounded workers keeping dependency I/O off the Execution Actor.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, RwLock};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use super::{SocketExecutionIntentPlanner, SocketExecutionOrderAdmission};
use crate::application::{
    DependencyWatermarks, ExecuteStrategyIntent, QuoteObservation, RiskAuthorizationContext,
    SubmitOrder,
};
use crate::domain::OrderCommitment;

enum PlanningRequest {
    AdvanceTime {
        event_time_unix_nanos: u64,
        reply: std::sync::mpsc::SyncSender<Result<(), String>>,
    },
    Plan {
        intent: Box<ExecuteStrategyIntent>,
        reply: std::sync::mpsc::SyncSender<Result<Vec<SubmitOrder>, String>>,
    },
    LatestQuote {
        instrument_id: String,
        market_id: Option<String>,
        reply: std::sync::mpsc::SyncSender<Result<Option<QuoteObservation>, String>>,
    },
}

enum AdmissionRequest {
    Validate {
        request: SubmitOrder,
        active_commitments: Vec<OrderCommitment>,
        reply: std::sync::mpsc::SyncSender<Result<OrderCommitment, String>>,
    },
    RiskContext {
        request: SubmitOrder,
        route: crate::application::ExecutionRouteCandidate,
        reply: std::sync::mpsc::SyncSender<Result<RiskAuthorizationContext, String>>,
    },
}

struct WorkerControl {
    stop: Arc<AtomicBool>,
    worker: Option<JoinHandle<()>>,
}

impl Drop for WorkerControl {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

#[derive(Default)]
struct DependencyCircuit {
    consecutive_failures: u32,
    open_until: Option<Instant>,
}

impl DependencyCircuit {
    fn permits(&self) -> bool {
        self.open_until.is_none_or(|until| Instant::now() >= until)
    }

    fn record(&mut self, result: &Result<(), String>) {
        let Err(error) = result else {
            self.consecutive_failures = 0;
            self.open_until = None;
            return;
        };
        if ![
            "transport",
            "http",
            "worker",
            "socket",
            "timeout",
            "stale",
            "not ready",
        ]
        .iter()
        .any(|marker| error.to_ascii_lowercase().contains(marker))
        {
            return;
        }
        self.consecutive_failures = self.consecutive_failures.saturating_add(1);
        if self.consecutive_failures >= 3 {
            self.open_until = Some(Instant::now() + Duration::from_secs(2));
        }
    }
}

fn send_bounded<R>(
    sender: &std::sync::mpsc::SyncSender<R>,
    request: R,
    queue_name: &str,
) -> Result<(), String> {
    let deadline = Instant::now() + Duration::from_secs(5);
    let mut request = Some(request);
    loop {
        match sender.try_send(request.take().expect("dependency request present")) {
            Ok(()) => return Ok(()),
            Err(std::sync::mpsc::TrySendError::Disconnected(_)) => {
                return Err(format!("execution {queue_name} worker is stopped"));
            },
            Err(std::sync::mpsc::TrySendError::Full(value)) => {
                if Instant::now() >= deadline {
                    return Err(format!("execution {queue_name} queue is full"));
                }
                request = Some(value);
                std::thread::sleep(Duration::from_millis(2));
            },
        }
    }
}

pub struct QueuedExecutionIntentPlanner {
    sender: std::sync::mpsc::SyncSender<PlanningRequest>,
    watermarks: Arc<RwLock<DependencyWatermarks>>,
    _control: WorkerControl,
    circuit: DependencyCircuit,
}

impl QueuedExecutionIntentPlanner {
    pub fn start(
        mut planner: SocketExecutionIntentPlanner,
        capacity: usize,
    ) -> Result<Self, String> {
        let (sender, receiver) = std::sync::mpsc::sync_channel(capacity.max(1));
        let watermarks = Arc::new(RwLock::new(planner.dependency_watermarks()));
        let stop = Arc::new(AtomicBool::new(false));
        let worker_stop = Arc::clone(&stop);
        let worker_watermarks = Arc::clone(&watermarks);
        let worker = std::thread::Builder::new()
            .name("execution-intent-planner".into())
            .spawn(move || {
                while !worker_stop.load(Ordering::Acquire) {
                    let request = match receiver.recv_timeout(Duration::from_millis(50)) {
                        Ok(request) => request,
                        Err(std::sync::mpsc::RecvTimeoutError::Timeout) => continue,
                        Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
                    };
                    match request {
                        PlanningRequest::AdvanceTime {
                            event_time_unix_nanos,
                            reply,
                        } => {
                            let _ = reply.send(planner.advance_time(event_time_unix_nanos));
                        },
                        PlanningRequest::Plan { intent, reply } => {
                            let _ = reply.send(planner.plan_intent(&intent));
                        },
                        PlanningRequest::LatestQuote {
                            instrument_id,
                            market_id,
                            reply,
                        } => {
                            let _ = reply
                                .send(planner.latest_quote(&instrument_id, market_id.as_deref()));
                        },
                    }
                    if let Ok(mut value) = worker_watermarks.write() {
                        *value = planner.dependency_watermarks();
                    }
                }
            })
            .map_err(|error| format!("start execution intent planner: {error}"))?;
        Ok(Self {
            sender,
            watermarks,
            _control: WorkerControl {
                stop,
                worker: Some(worker),
            },
            circuit: DependencyCircuit::default(),
        })
    }

    fn request<T>(
        &mut self,
        request: PlanningRequest,
        reply: std::sync::mpsc::Receiver<Result<T, String>>,
    ) -> Result<T, String> {
        if !self.circuit.permits() {
            return Err("execution planning dependency circuit is open".into());
        }
        send_bounded(&self.sender, request, "intent planning")?;
        let result = reply
            .recv()
            .map_err(|_| "execution intent planner did not respond".to_string())?;
        self.circuit
            .record(&result.as_ref().map(|_| ()).map_err(Clone::clone));
        result
    }
}

impl QueuedExecutionIntentPlanner {
    pub(crate) fn advance_time(&mut self, event_time_unix_nanos: u64) -> Result<(), String> {
        let (tx, rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            PlanningRequest::AdvanceTime {
                event_time_unix_nanos,
                reply: tx,
            },
            rx,
        )
    }

    pub(crate) fn dependency_watermarks(&self) -> DependencyWatermarks {
        self.watermarks
            .read()
            .map(|value| value.clone())
            .unwrap_or_default()
    }

    pub(crate) fn plan_intent(
        &mut self,
        intent: &ExecuteStrategyIntent,
    ) -> Result<Vec<SubmitOrder>, String> {
        let (tx, rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            PlanningRequest::Plan {
                intent: Box::new(intent.clone()),
                reply: tx,
            },
            rx,
        )
    }

    pub(crate) fn latest_quote(
        &mut self,
        instrument_id: &str,
        market_id: Option<&str>,
    ) -> Result<Option<QuoteObservation>, String> {
        let (tx, rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            PlanningRequest::LatestQuote {
                instrument_id: instrument_id.into(),
                market_id: market_id.map(str::to_owned),
                reply: tx,
            },
            rx,
        )
    }
}

pub struct QueuedExecutionOrderAdmission {
    sender: std::sync::mpsc::SyncSender<AdmissionRequest>,
    watermarks: Arc<RwLock<DependencyWatermarks>>,
    _control: WorkerControl,
    circuit: DependencyCircuit,
}

impl QueuedExecutionOrderAdmission {
    pub fn start(
        mut admission: SocketExecutionOrderAdmission,
        capacity: usize,
    ) -> Result<Self, String> {
        let (sender, receiver) = std::sync::mpsc::sync_channel(capacity.max(1));
        let watermarks = Arc::new(RwLock::new(admission.dependency_watermarks()));
        let stop = Arc::new(AtomicBool::new(false));
        let worker_stop = Arc::clone(&stop);
        let worker_watermarks = Arc::clone(&watermarks);
        let worker = std::thread::Builder::new()
            .name("execution-order-admission".into())
            .spawn(move || {
                while !worker_stop.load(Ordering::Acquire) {
                    let request = match receiver.recv_timeout(Duration::from_millis(50)) {
                        Ok(request) => request,
                        Err(std::sync::mpsc::RecvTimeoutError::Timeout) => continue,
                        Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
                    };
                    match request {
                        AdmissionRequest::Validate {
                            request,
                            active_commitments,
                            reply,
                        } => {
                            let _ =
                                reply.send(admission.validate_order(&request, &active_commitments));
                        },
                        AdmissionRequest::RiskContext {
                            request,
                            route,
                            reply,
                        } => {
                            let _ =
                                reply.send(admission.risk_authorization_context(&request, &route));
                        },
                    }
                    if let Ok(mut value) = worker_watermarks.write() {
                        *value = admission.dependency_watermarks();
                    }
                }
            })
            .map_err(|error| format!("start execution order admission: {error}"))?;
        Ok(Self {
            sender,
            watermarks,
            _control: WorkerControl {
                stop,
                worker: Some(worker),
            },
            circuit: DependencyCircuit::default(),
        })
    }

    fn request<T>(
        &mut self,
        request: AdmissionRequest,
        reply: std::sync::mpsc::Receiver<Result<T, String>>,
    ) -> Result<T, String> {
        if !self.circuit.permits() {
            return Err("execution admission dependency circuit is open".into());
        }
        send_bounded(&self.sender, request, "order admission")?;
        let result = reply
            .recv()
            .map_err(|_| "execution order admission did not respond".to_string())?;
        self.circuit
            .record(&result.as_ref().map(|_| ()).map_err(Clone::clone));
        result
    }
}

impl QueuedExecutionOrderAdmission {
    pub(crate) fn dependency_watermarks(&self) -> DependencyWatermarks {
        self.watermarks
            .read()
            .map(|value| value.clone())
            .unwrap_or_default()
    }

    pub(crate) fn validate_order(
        &mut self,
        request: &SubmitOrder,
        active_commitments: &[OrderCommitment],
    ) -> Result<OrderCommitment, String> {
        let (tx, rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            AdmissionRequest::Validate {
                request: request.clone(),
                active_commitments: active_commitments.to_vec(),
                reply: tx,
            },
            rx,
        )
    }

    pub(crate) fn risk_authorization_context(
        &mut self,
        request: &SubmitOrder,
        route: &crate::application::ExecutionRouteCandidate,
    ) -> Result<RiskAuthorizationContext, String> {
        let (tx, rx) = std::sync::mpsc::sync_channel(1);
        self.request(
            AdmissionRequest::RiskContext {
                request: request.clone(),
                route: route.clone(),
                reply: tx,
            },
            rx,
        )
    }
}

#[cfg(test)]
mod tests {
    use std::time::{Duration, Instant};

    use super::DependencyCircuit;

    #[test]
    fn dependency_circuit_opens_after_repeated_transport_failures() {
        let mut circuit = DependencyCircuit::default();
        let failure = Err("transport timeout".to_string());
        assert!(circuit.permits());
        circuit.record(&failure);
        circuit.record(&failure);
        assert!(circuit.permits());
        circuit.record(&failure);
        assert!(!circuit.permits());
        circuit.open_until = Some(Instant::now() - Duration::from_millis(1));
        assert!(circuit.permits());
    }

    #[test]
    fn validation_errors_do_not_open_dependency_circuit() {
        let mut circuit = DependencyCircuit::default();
        let failure = Err("order quantity must be positive".to_string());
        for _ in 0..5 {
            circuit.record(&failure);
        }
        assert!(circuit.permits());
    }
}
