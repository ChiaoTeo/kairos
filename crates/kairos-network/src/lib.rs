//! Small, provider-neutral network primitives shared by exchange adapters.

use std::net::{IpAddr, Ipv4Addr};

#[derive(Debug, Clone, Default)]
pub struct NetworkConfig {
    pub preferred_local_ips: Vec<IpAddr>,
}

pub fn is_private_ipv4(ip: Ipv4Addr) -> bool {
    match ip.octets() {
        [10, _, _, _] => true,
        [172, second, _, _] if (16..=31).contains(&second) => true,
        [192, 168, _, _] => true,
        _ => false,
    }
}

pub fn choose_local_ip(config: &NetworkConfig, candidates: &[IpAddr]) -> Option<IpAddr> {
    config
        .preferred_local_ips
        .iter()
        .copied()
        .find(|ip| candidates.contains(ip))
        .or_else(|| candidates.iter().copied().find(is_global_ip))
        .or_else(|| candidates.first().copied())
}

fn is_global_ip(ip: &IpAddr) -> bool {
    match ip {
        IpAddr::V4(value) => {
            !value.is_loopback() && !value.is_link_local() && !is_private_ipv4(*value)
        }
        IpAddr::V6(value) => {
            !value.is_loopback() && !value.is_unspecified() && !value.is_unique_local()
        }
    }
}
