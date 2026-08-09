//! External account connection capabilities.
//!
//! This module owns only connection contracts and external facts.  It does
//! not depend on, or implement protocols owned by, the account business
//! module.

use std::sync::{
    atomic::{AtomicBool, Ordering},
    mpsc::{self, Receiver, TryRecvError},
    Arc,
};
use std::thread::JoinHandle;

use super::connection::Connection;
use super::error::IntegrationError;
use crate::domain::account::{
    ExternalAccountEvent, ExternalAccountSegment, ExternalAccountSnapshot,
};

pub trait AccountReadConnection: Connection {
    fn fetch_account(
        &mut self,
        segment: &ExternalAccountSegment,
    ) -> Result<ExternalAccountSnapshot, IntegrationError>;
}

pub trait AccountMarketProfileConnection: Connection {
    fn fetch_market_profile(
        &mut self,
        request: &ExternalMarketProfileRequest,
    ) -> Result<ExternalMarketProfile, IntegrationError>;
}

pub trait AccountEventStreamConnection: Connection {
    fn next_account_event(&mut self) -> Result<Option<ExternalAccountEvent>, IntegrationError>;
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExternalMarketProfileRequest {
    pub account_id: String,
    pub segment_key: String,
    pub market_id: String,
    pub source_symbol: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExternalMarketProfile {
    pub account_id: String,
    pub segment_key: String,
    pub market_id: String,
    pub account_model: Option<crate::domain::ExternalAccountModel>,
    pub margin_mode: Option<String>,
    pub position_mode: Option<String>,
    pub maker_fee: Option<crate::domain::ExternalDecimal>,
    pub taker_fee: Option<crate::domain::ExternalDecimal>,
    pub fee_currency: Option<String>,
    pub fee_discount: Option<crate::domain::ExternalDecimal>,
    pub fee_tier: Option<String>,
    pub source: String,
    pub observed_at_unix_nanos: u64,
}

pub struct BufferedIntegrationAccountStream {
    receiver: Receiver<Result<Option<ExternalAccountEvent>, String>>,
    stop: Arc<AtomicBool>,
    pending: Arc<std::sync::atomic::AtomicUsize>,
    worker: Option<JoinHandle<()>>,
}

impl BufferedIntegrationAccountStream {
    pub fn next_event(&mut self) -> Result<Option<ExternalAccountEvent>, String> {
        match self.receiver.try_recv() {
            Ok(event) => {
                self.pending.fetch_sub(1, Ordering::Relaxed);
                event
            }
            Err(TryRecvError::Empty) => Ok(None),
            Err(TryRecvError::Disconnected) => Err("account stream worker stopped".into()),
        }
    }

    pub fn pending_events(&self) -> usize {
        self.pending.load(Ordering::Relaxed)
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
        let pending = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let worker_stop = Arc::clone(&stop);
        let worker_pending = Arc::clone(&pending);
        let mut connection = self.connection;
        let worker = std::thread::spawn(move || {
            while !worker_stop.load(Ordering::Relaxed) {
                match connection.next_account_event() {
                    Ok(Some(event)) => {
                        worker_pending.fetch_add(1, Ordering::Relaxed);
                        if sender.send(Ok(Some(event))).is_err() {
                            worker_pending.fetch_sub(1, Ordering::Relaxed);
                            break;
                        }
                    }
                    Ok(None) => std::thread::sleep(std::time::Duration::from_millis(5)),
                    Err(error) => {
                        worker_pending.fetch_add(1, Ordering::Relaxed);
                        let _ = sender.send(Err(error.to_string()));
                        break;
                    }
                }
            }
        });
        BufferedIntegrationAccountStream {
            receiver,
            stop,
            pending,
            worker: Some(worker),
        }
    }
}

pub struct IntegrationAccountStream<C> {
    connection: C,
}

impl<C: AccountEventStreamConnection + ?Sized> AccountEventStreamConnection for Box<C> {
    fn next_account_event(&mut self) -> Result<Option<ExternalAccountEvent>, IntegrationError> {
        (**self).next_account_event()
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

impl<C: Connection + ?Sized> Connection for Box<C> {
    fn identity(&self) -> &crate::domain::ConnectionIdentity {
        (**self).identity()
    }
    fn state(&self) -> &crate::domain::ConnectionState {
        (**self).state()
    }
    fn start(&mut self) -> Result<(), String> {
        (**self).start()
    }
    fn stop(&mut self) -> Result<(), String> {
        (**self).stop()
    }
    fn reconnect(&mut self) -> Result<(), String> {
        (**self).reconnect()
    }
    fn health(&self) -> crate::domain::ConnectionHealth {
        (**self).health()
    }
}

// Kept private to integration; account-side adapters are defined by the
// consumer module/composition root.
impl Drop for BufferedIntegrationAccountStream {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        self.worker.take();
        let _ = self.receiver.try_recv();
    }
}
