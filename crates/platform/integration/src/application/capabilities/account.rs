//! Account read, inspection, profile, and private-event capabilities.

mod state {
    //! External account connection capabilities.
    //!
    //! This module owns only connection contracts and external facts.  It does
    //! not depend on, or implement protocols owned by, the account business
    //! module.

    use std::future::Future;
    use std::sync::{
        atomic::{AtomicBool, Ordering},
        mpsc::{self, Receiver, TryRecvError, TrySendError},
        Arc, Mutex,
    };
    use std::thread::JoinHandle;
    use std::time::Duration;

    use crate::application::capabilities::account_facts::{
        ExternalAccountEvent, ExternalAccountEventEnvelope, ExternalAccountSegment,
        ExternalAccountSnapshot,
    };
    use crate::application::IntegrationError;
    use kairos_primitives::{AccountId, Currency, MarketId, SegmentKey, Symbol, UnixNanos};

    pub trait AccountReadConnection: Send {
        fn fetch_account(
            &mut self,
            segment: &ExternalAccountSegment,
        ) -> Result<ExternalAccountSnapshot, IntegrationError>;
    }

    pub trait AsyncAccountReadConnection: Send {
        fn fetch_account(
            &mut self,
            segment: &ExternalAccountSegment,
        ) -> impl Future<Output = Result<ExternalAccountSnapshot, IntegrationError>> + Send;
    }

    pub trait AccountMarketProfileConnection: Send {
        fn fetch_market_profile(
            &mut self,
            request: &ExternalMarketProfileRequest,
        ) -> Result<ExternalMarketProfile, IntegrationError>;
    }

    pub trait AsyncAccountMarketProfileConnection: Send {
        fn fetch_market_profile(
            &mut self,
            request: &ExternalMarketProfileRequest,
        ) -> impl Future<Output = Result<ExternalMarketProfile, IntegrationError>> + Send;
    }

    /// Result of one bounded wait on a long-lived account event source.
    ///
    /// `Idle` means that the supplied wait elapsed without a provider event. It
    /// is not end-of-stream and it is deliberately distinct from an empty query
    /// result.
    pub enum AccountEventReceive {
        Event(ExternalAccountEvent),
        Idle,
    }

    pub trait AccountEventStreamConnection: Send {
        fn connect_channel(&mut self) -> Result<(), IntegrationError>;
        fn disconnect_channel(&mut self) -> Result<(), IntegrationError>;
        fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
            self.disconnect_channel()?;
            self.connect_channel()
        }
        fn channel_health(&self) -> crate::domain::ConnectionHealth;

        /// Wait for the next provider event, returning `Idle` when `timeout`
        /// elapses. Network calls still obey their provider-specific timeouts.
        ///
        /// Implementations must not emulate waiting with a high-frequency
        /// `try_recv` loop. This method is intended for a business-owned IO
        /// worker, never for an actor/runtime thread.
        fn recv_account_event(
            &mut self,
            timeout: Duration,
        ) -> Result<AccountEventReceive, IntegrationError>;
    }

    /// Async-first long-lived account event capability. It owns only its real
    /// channel lifecycle and does not inherit a universal connection protocol.
    pub trait AsyncAccountEventSource: Send {
        fn connect_channel(&mut self) -> impl Future<Output = Result<(), IntegrationError>> + Send;
        fn disconnect_channel(
            &mut self,
        ) -> impl Future<Output = Result<(), IntegrationError>> + Send;
        fn reconnect_channel(
            &mut self,
        ) -> impl Future<Output = Result<(), IntegrationError>> + Send {
            async {
                self.disconnect_channel().await?;
                self.connect_channel().await
            }
        }
        fn channel_health(&self) -> crate::domain::ConnectionHealth;
        fn next_account_event(
            &mut self,
        ) -> impl Future<Output = Result<ExternalAccountEventEnvelope, IntegrationError>> + Send;
    }

    #[derive(Clone, Debug, Eq, PartialEq)]
    pub struct ExternalMarketProfileRequest {
        pub account_id: AccountId,
        pub segment_key: SegmentKey,
        pub market_id: MarketId,
        pub source_symbol: Symbol,
    }

    #[derive(Clone, Debug, Eq, PartialEq)]
    pub struct ExternalMarketProfile {
        pub account_id: AccountId,
        pub segment_key: SegmentKey,
        pub market_id: MarketId,
        pub account_model: Option<super::super::account_facts::ExternalAccountModel>,
        pub margin_mode: Option<String>,
        pub position_mode: Option<String>,
        pub maker_fee: Option<super::super::account_facts::ExternalDecimal>,
        pub taker_fee: Option<super::super::account_facts::ExternalDecimal>,
        pub fee_currency: Option<Currency>,
        pub fee_discount: Option<super::super::account_facts::ExternalDecimal>,
        pub fee_tier: Option<String>,
        pub source: String,
        pub observed_at_unix_nanos: UnixNanos,
    }

    pub struct BufferedIntegrationAccountStream {
        receiver: Receiver<Result<ExternalAccountEvent, String>>,
        stop: Arc<AtomicBool>,
        overflowed: Arc<AtomicBool>,
        pending: Arc<std::sync::atomic::AtomicUsize>,
        wakeup: Arc<Mutex<Option<Arc<tokio::sync::Notify>>>>,
        worker: Option<JoinHandle<()>>,
    }

    impl BufferedIntegrationAccountStream {
        pub fn next_event(&mut self) -> Result<Option<ExternalAccountEvent>, String> {
            match self.receiver.try_recv() {
                Ok(Ok(event)) => {
                    self.pending.fetch_sub(1, Ordering::Relaxed);
                    Ok(Some(event))
                }
                Ok(Err(error)) => {
                    self.pending.fetch_sub(1, Ordering::Relaxed);
                    Err(error)
                }
                Err(TryRecvError::Empty) => Ok(None),
                Err(TryRecvError::Disconnected)
                    if self.overflowed.swap(false, Ordering::AcqRel) =>
                {
                    Err(
                        "account event queue overflowed; snapshot resynchronization is required"
                            .into(),
                    )
                }
                Err(TryRecvError::Disconnected) => Err("account stream worker stopped".into()),
            }
        }

        pub fn pending_events(&self) -> usize {
            self.pending.load(Ordering::Relaxed)
                + usize::from(self.overflowed.load(Ordering::Acquire))
        }

        /// Register the business runtime wakeup used after an event or stream
        /// failure is queued. Registration after worker startup is safe: an
        /// already-pending item immediately grants a notification permit.
        pub fn register_wakeup(&mut self, wakeup: Arc<tokio::sync::Notify>) {
            *self.wakeup.lock().expect("account stream wakeup poisoned") =
                Some(Arc::clone(&wakeup));
            if self.pending_events() > 0 || self.overflowed.load(Ordering::Acquire) {
                wakeup.notify_one();
            }
        }
    }

    impl<C> IntegrationAccountStream<C> {
        pub fn new(connection: C) -> Self {
            Self { connection }
        }

        pub fn connection(&self) -> &C {
            &self.connection
        }
        pub fn connection_mut(&mut self) -> &mut C {
            &mut self.connection
        }

        pub fn buffered(self) -> BufferedIntegrationAccountStream
        where
            C: AccountEventStreamConnection + Send + 'static,
        {
            let (sender, receiver) = mpsc::sync_channel(256);
            let stop = Arc::new(AtomicBool::new(false));
            let overflowed = Arc::new(AtomicBool::new(false));
            let pending = Arc::new(std::sync::atomic::AtomicUsize::new(0));
            let wakeup = Arc::new(Mutex::new(None::<Arc<tokio::sync::Notify>>));
            let worker_stop = Arc::clone(&stop);
            let worker_overflowed = Arc::clone(&overflowed);
            let worker_pending = Arc::clone(&pending);
            let worker_wakeup = Arc::clone(&wakeup);
            let mut connection = self.connection;
            let worker = std::thread::spawn(move || {
                const RECEIVE_WAIT: Duration = Duration::from_millis(250);
                while !worker_stop.load(Ordering::Relaxed) {
                    let (queued, reconnect) = match connection.recv_account_event(RECEIVE_WAIT) {
                        Ok(AccountEventReceive::Event(event)) => (Some(Ok(event)), false),
                        Ok(AccountEventReceive::Idle) => (None, false),
                        Err(error) => (Some(Err(error.to_string())), true),
                    };
                    if let Some(queued) = queued {
                        worker_pending.fetch_add(1, Ordering::Relaxed);
                        match sender.try_send(queued) {
                            Ok(()) => {
                                if let Some(wakeup) = worker_wakeup
                                    .lock()
                                    .expect("account stream wakeup poisoned")
                                    .as_ref()
                                {
                                    wakeup.notify_one();
                                }
                            }
                            Err(TrySendError::Full(_)) => {
                                worker_pending.fetch_sub(1, Ordering::Relaxed);
                                worker_overflowed.store(true, Ordering::Release);
                                if let Some(wakeup) = worker_wakeup
                                    .lock()
                                    .expect("account stream wakeup poisoned")
                                    .as_ref()
                                {
                                    wakeup.notify_one();
                                }
                                break;
                            }
                            Err(TrySendError::Disconnected(_)) => {
                                worker_pending.fetch_sub(1, Ordering::Relaxed);
                                break;
                            }
                        }
                    }
                    if reconnect {
                        // A private account stream is long-lived. A read failure
                        // is observable to the business consumer, then the IO
                        // worker reconnects without making the Actor a lifecycle
                        // owner.
                        if connection.reconnect_channel().is_err() {
                            for _ in 0..4 {
                                if worker_stop.load(Ordering::Relaxed) {
                                    break;
                                }
                                std::thread::park_timeout(RECEIVE_WAIT);
                            }
                        }
                    }
                }
                let _ = connection.disconnect_channel();
            });
            BufferedIntegrationAccountStream {
                receiver,
                stop,
                overflowed,
                pending,
                wakeup,
                worker: Some(worker),
            }
        }
    }

    pub struct IntegrationAccountStream<C> {
        connection: C,
    }

    impl<C: AccountEventStreamConnection + ?Sized> AccountEventStreamConnection for Box<C> {
        fn connect_channel(&mut self) -> Result<(), IntegrationError> {
            (**self).connect_channel()
        }
        fn disconnect_channel(&mut self) -> Result<(), IntegrationError> {
            (**self).disconnect_channel()
        }
        fn reconnect_channel(&mut self) -> Result<(), IntegrationError> {
            (**self).reconnect_channel()
        }
        fn channel_health(&self) -> crate::domain::ConnectionHealth {
            (**self).channel_health()
        }
        fn recv_account_event(
            &mut self,
            timeout: Duration,
        ) -> Result<AccountEventReceive, IntegrationError> {
            (**self).recv_account_event(timeout)
        }
    }

    impl<C: AccountReadConnection + ?Sized> AccountReadConnection for Box<C> {
        fn fetch_account(
            &mut self,
            segment: &ExternalAccountSegment,
        ) -> Result<ExternalAccountSnapshot, IntegrationError> {
            (**self).fetch_account(segment)
        }
    }

    impl<C: AccountMarketProfileConnection + ?Sized> AccountMarketProfileConnection for Box<C> {
        fn fetch_market_profile(
            &mut self,
            request: &ExternalMarketProfileRequest,
        ) -> Result<ExternalMarketProfile, IntegrationError> {
            (**self).fetch_market_profile(request)
        }
    }

    // Kept private to integration; account-side adapters are defined by the
    // consumer module/composition root.
    impl Drop for BufferedIntegrationAccountStream {
        fn drop(&mut self) {
            self.stop.store(true, Ordering::Relaxed);
            if let Some(worker) = self.worker.take() {
                worker.thread().unpark();
            }
            let _ = self.receiver.try_recv();
        }
    }
}

pub use state::*;

mod inspection {
    //! Normalized credential inspection used to discover account capabilities.

    use std::collections::BTreeMap;
    use std::future::Future;

    #[derive(Clone, Debug, Default, Eq, PartialEq, serde::Serialize, serde::Deserialize)]
    pub struct ExternalAccountCredentialProfile {
        pub remote_identity: Option<String>,
        pub account_type: Option<String>,
        pub permissions: Vec<String>,
        pub segments: Vec<String>,
        pub attributes: BTreeMap<String, String>,
    }

    pub trait AccountCredentialInspectionConnection: Send {
        fn inspect_credential(&mut self) -> Result<ExternalAccountCredentialProfile, String>;
    }

    pub trait AsyncAccountCredentialInspectionConnection: Send {
        fn inspect_credential(
            &mut self,
        ) -> impl Future<
            Output = Result<ExternalAccountCredentialProfile, crate::application::IntegrationError>,
        > + Send;
    }
}

pub use inspection::*;
