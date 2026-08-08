//! Capability selection is explicit and injected by composition.

use crate::application::{Connection, ConnectionSpec};

pub(crate) trait IntegrationConnectionFactory: Send + Sync {
    fn connect(&self, spec: &ConnectionSpec) -> Result<Box<dyn Connection>, String>;
}

pub(crate) trait GatewaySelector: IntegrationConnectionFactory {
    fn supports(&self, spec: &ConnectionSpec) -> bool;
}

/// Composition-owned registry for selecting exactly one provider gateway.
///
/// The registry does not own business state and does not expose provider
/// payloads. It only resolves a validated connection specification to the
/// capability-specific `Connection` selected by the composition root.
pub(crate) struct GatewayRegistry {
    gateways: Vec<Box<dyn GatewaySelector>>,
}

impl GatewayRegistry {
    pub fn new() -> Self {
        Self {
            gateways: Vec::new(),
        }
    }

    pub fn register<G>(&mut self, gateway: G)
    where
        G: GatewaySelector + 'static,
    {
        self.gateways.push(Box::new(gateway));
    }

    pub fn connect(&self, spec: &ConnectionSpec) -> Result<Box<dyn Connection>, String> {
        spec.validate()?;
        self.gateways
            .iter()
            .find(|gateway| gateway.supports(spec))
            .ok_or_else(|| {
                format!(
                    "no integration gateway supports route={:?}, product={:?}, capability={:?}, transport={:?}",
                    spec.route, spec.product, spec.capability, spec.transport
                )
            })?
            .connect(spec)
    }
}

impl Default for GatewayRegistry {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::GatewayRegistry;
    use crate::application::ConnectionSpec;
    use crate::domain::{AccessScope, IntegrationCapability, ProductFamily, TransportKind};
    use crate::services::gateways::binance::BinanceReferenceFactory;

    #[test]
    fn registry_selects_a_gateway_by_connection_spec() {
        let mut registry = GatewayRegistry::new();
        registry.register(
            BinanceReferenceFactory::new("https://api.binance.com/api/v3/exchangeInfo").unwrap(),
        );
        let spec = ConnectionSpec {
            connection_id: "reference.binance.spot.rest".into(),
            route: crate::domain::IntegrationRoute::exchange("binance"),
            product: Some(ProductFamily::Spot),
            access: AccessScope::Public,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::Reference,
            credential_id: None,
            asset_type: None,
        };
        let connection = registry.connect(&spec).unwrap();
        assert_eq!(connection.identity().route.primary().id, "binance");
    }

    #[test]
    fn registry_reports_unsupported_specs() {
        let registry = GatewayRegistry::new();
        let spec = ConnectionSpec {
            connection_id: "reference.unknown".into(),
            route: crate::domain::IntegrationRoute::exchange("unknown"),
            product: None,
            access: AccessScope::Public,
            transport: TransportKind::Rest,
            capability: IntegrationCapability::Reference,
            credential_id: None,
            asset_type: None,
        };
        let error = match registry.connect(&spec) {
            Ok(_) => panic!("unsupported spec unexpectedly connected"),
            Err(error) => error,
        };
        assert!(error.contains("no integration gateway"));
    }
}
