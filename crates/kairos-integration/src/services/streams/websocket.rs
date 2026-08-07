//! Provider-neutral WebSocket session adapter.
//!
//! A provider gateway owns frame parsing and subscription vocabulary. This
//! adapter owns only the connection lifecycle and exposes the same
//! `MarketStreamConnection` contract as REST polling and replay sources.

#![allow(dead_code)]

use crate::application::connection::Connection;
use crate::application::error::IntegrationError;
use crate::application::market_stream::{
    MarketStreamConnection, MarketSubscription, SubscriptionId,
};
use crate::domain::{
    ConnectionHealth, ConnectionIdentity, ConnectionLifecycle, ConnectionState, MarketEvent,
};

pub trait WebSocketEventSource: Send {
    fn start(&mut self) -> Result<(), String>;
    fn stop(&mut self) -> Result<(), String>;
    fn reconnect(&mut self) -> Result<(), String>;
    fn subscribe(&mut self, request: &MarketSubscription) -> Result<SubscriptionId, String>;
    fn unsubscribe(&mut self, subscription: SubscriptionId) -> Result<(), String>;
    fn next_event(&mut self) -> Result<Option<MarketEvent>, String>;
}

pub struct WebSocketMarketStream<S> {
    identity: ConnectionIdentity,
    state: ConnectionState,
    source: S,
}

impl<S: WebSocketEventSource> WebSocketMarketStream<S> {
    pub fn new(identity: ConnectionIdentity, source: S) -> Self {
        Self {
            state: ConnectionState::new(identity.clone()),
            identity,
            source,
        }
    }
}

impl<S: WebSocketEventSource> Connection for WebSocketMarketStream<S> {
    fn identity(&self) -> &ConnectionIdentity {
        &self.identity
    }

    fn state(&self) -> &ConnectionState {
        &self.state
    }

    fn start(&mut self) -> Result<(), String> {
        self.source.start()?;
        self.state.lifecycle = ConnectionLifecycle::Ready;
        self.state.last_error = None;
        Ok(())
    }

    fn stop(&mut self) -> Result<(), String> {
        self.source.stop()?;
        self.state.lifecycle = ConnectionLifecycle::Stopped;
        Ok(())
    }

    fn reconnect(&mut self) -> Result<(), String> {
        self.source.reconnect()?;
        self.state.lifecycle = ConnectionLifecycle::Ready;
        self.state.reconnect_count += 1;
        self.state.last_error = None;
        Ok(())
    }

    fn health(&self) -> ConnectionHealth {
        ConnectionHealth {
            lifecycle: self.state.lifecycle,
            healthy: self.state.lifecycle == ConnectionLifecycle::Ready,
            authenticated: self.state.authenticated,
            last_error: self.state.last_error.clone(),
        }
    }
}

impl<S: WebSocketEventSource> MarketStreamConnection for WebSocketMarketStream<S> {
    fn subscribe(
        &mut self,
        request: MarketSubscription,
    ) -> Result<SubscriptionId, IntegrationError> {
        if self.state.lifecycle != ConnectionLifecycle::Ready {
            return Err(IntegrationError::NotReady);
        }
        self.source
            .subscribe(&request)
            .map_err(IntegrationError::Transport)
    }

    fn unsubscribe(&mut self, subscription: SubscriptionId) -> Result<(), IntegrationError> {
        self.source
            .unsubscribe(subscription)
            .map_err(IntegrationError::Transport)
    }

    fn next_event(&mut self) -> Result<Option<MarketEvent>, IntegrationError> {
        self.source.next_event().map_err(|error| {
            self.state.last_error = Some(error.clone());
            self.state.lifecycle = ConnectionLifecycle::Degraded;
            IntegrationError::Transport(error)
        })
    }
}
