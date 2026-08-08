//! Capability lookup for the integration composition root.
//!
//! Provider registration lives in `integration.rs`; this module owns the
//! provider-neutral selection of an already registered connection.

use super::Integration;
use crate::application::{
    AccountCredentialInspectionConnection, AccountEventStreamConnection,
    AccountMarketProfileConnection, AccountReadConnection, Connection, ConnectionSpec,
    EarnConnection, ExecutionStreamConnection, IntegrationError, MarketStreamConnection,
    OrderEntryConnection, OrderQueryConnection, TransferConnection,
};
use crate::domain::IntegrationCapability;

impl Integration {
    pub fn connect_account(
        &self,
        spec: &ConnectionSpec,
    ) -> Result<Box<dyn AccountReadConnection>, IntegrationError> {
        self.connect_registered(
            spec,
            IntegrationCapability::AccountRead,
            "connection spec is not account read",
            &self.accounts,
        )
    }

    pub fn connect_account_credential_inspection(
        &self,
        spec: &ConnectionSpec,
    ) -> Result<Box<dyn AccountCredentialInspectionConnection>, IntegrationError> {
        self.connect_registered(
            spec,
            IntegrationCapability::AccountCredentialInspection,
            "connection spec is not account credential inspection",
            &self.credential_inspections,
        )
    }

    pub fn connect_account_market_profile(
        &self,
        spec: &ConnectionSpec,
    ) -> Result<Box<dyn AccountMarketProfileConnection>, IntegrationError> {
        self.connect_registered(
            spec,
            IntegrationCapability::AccountMarketProfileRead,
            "connection spec is not an account market profile read",
            &self.account_market_profiles,
        )
    }

    pub fn connect_order_entry(
        &self,
        spec: &ConnectionSpec,
    ) -> Result<Box<dyn OrderEntryConnection>, IntegrationError> {
        self.connect_registered(
            spec,
            IntegrationCapability::OrderEntry,
            "connection spec is not order entry",
            &self.order_entries,
        )
    }

    pub fn connect_order_query(
        &self,
        spec: &ConnectionSpec,
    ) -> Result<Box<dyn OrderQueryConnection>, IntegrationError> {
        self.connect_registered(
            spec,
            IntegrationCapability::OrderRead,
            "connection spec is not order read",
            &self.order_queries,
        )
    }

    pub fn connect_account_stream(
        &self,
        spec: &ConnectionSpec,
    ) -> Result<Box<dyn AccountEventStreamConnection>, IntegrationError> {
        self.connect_registered(
            spec,
            IntegrationCapability::AccountStream,
            "connection spec is not an account stream",
            &self.account_streams,
        )
    }

    pub fn connect_execution_stream(
        &self,
        spec: &ConnectionSpec,
    ) -> Result<Box<dyn ExecutionStreamConnection>, IntegrationError> {
        self.connect_registered(
            spec,
            IntegrationCapability::ExecutionStream,
            "connection spec is not execution stream",
            &self.execution_streams,
        )
    }

    pub fn connect_earn(
        &self,
        spec: &ConnectionSpec,
    ) -> Result<Box<dyn EarnConnection>, IntegrationError> {
        if spec.capability != IntegrationCapability::Earn {
            return Err(IntegrationError::InvalidRequest(
                "connection spec is not earn".into(),
            ));
        }
        self.earns
            .iter()
            .find(|entry| {
                entry.route.matches_primary(&spec.route) && entry.transport == spec.transport
            })
            .ok_or(IntegrationError::UnsupportedOperation)
            .and_then(|entry| (entry.open)())
    }

    pub fn connect_transfer(
        &self,
        spec: &ConnectionSpec,
    ) -> Result<Box<dyn TransferConnection>, IntegrationError> {
        if spec.capability != IntegrationCapability::Transfer {
            return Err(IntegrationError::InvalidRequest(
                "connection spec is not transfer".into(),
            ));
        }
        self.transfers
            .iter()
            .find(|entry| {
                entry.route.matches_primary(&spec.route) && entry.transport == spec.transport
            })
            .ok_or(IntegrationError::UnsupportedOperation)
            .and_then(|entry| (entry.open)())
    }

    pub fn connect(&self, spec: &ConnectionSpec) -> Result<Box<dyn Connection>, IntegrationError> {
        self.gateways
            .connect(spec)
            .map_err(IntegrationError::InvalidRequest)
    }

    pub fn connect_market_stream(
        &self,
        spec: &ConnectionSpec,
    ) -> Result<Box<dyn MarketStreamConnection>, IntegrationError> {
        self.connect_registered(
            spec,
            IntegrationCapability::MarketStream,
            "connection spec is not a market stream",
            &self.market_streams,
        )
    }

    fn connect_registered<E, F>(
        &self,
        spec: &ConnectionSpec,
        capability: IntegrationCapability,
        message: &str,
        entries: &[E],
    ) -> Result<Box<F>, IntegrationError>
    where
        E: RegisteredConnection<F>,
        F: ?Sized,
    {
        if spec.capability != capability {
            return Err(IntegrationError::InvalidRequest(message.into()));
        }
        entries
            .iter()
            .find(|entry| entry.matches(spec))
            .ok_or(IntegrationError::UnsupportedOperation)
            .and_then(|entry| entry.open())
    }
}

trait RegisteredConnection<T: ?Sized> {
    fn matches(&self, spec: &ConnectionSpec) -> bool;
    fn open(&self) -> Result<Box<T>, IntegrationError>;
}

macro_rules! registered_connection {
    ($entry:ty, $target:ty) => {
        impl RegisteredConnection<$target> for $entry {
            fn matches(&self, spec: &ConnectionSpec) -> bool {
                self.route.matches_primary(&spec.route)
                    && self.product == spec.product
                    && self.transport == spec.transport
            }

            fn open(&self) -> Result<Box<$target>, IntegrationError> {
                (self.open)()
            }
        }
    };
}

registered_connection!(super::AccountEntry, dyn AccountReadConnection);
registered_connection!(
    super::AccountMarketProfileEntry,
    dyn AccountMarketProfileConnection
);
registered_connection!(super::OrderEntry, dyn OrderEntryConnection);
registered_connection!(super::OrderQueryEntry, dyn OrderQueryConnection);
registered_connection!(super::AccountStreamEntry, dyn AccountEventStreamConnection);
registered_connection!(super::ExecutionStreamEntry, dyn ExecutionStreamConnection);
registered_connection!(super::MarketStreamEntry, dyn MarketStreamConnection);
registered_connection!(
    super::CredentialInspectionEntry,
    dyn AccountCredentialInspectionConnection
);
