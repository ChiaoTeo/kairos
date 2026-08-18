//! Provider endpoint defaults used while constructing Conflux-owned connections.

pub fn default_endpoint(provider: &str) -> &'static str {
    match provider {
        "binance-spot-rest" => "https://api.binance.com",
        "binance-spot-websocket" => "wss://stream.binance.com:9443/ws",
        "binance-equity" => "https://api.binance.com",
        "binance-usdm-futures-websocket" => "wss://fstream.binance.com/ws",
        "binance-coinm-futures-websocket" => "wss://dstream.binance.com/ws",
        "binance-usdm-futures-rest" => "https://fapi.binance.com",
        "binance-coinm-futures-rest" => "https://dapi.binance.com",
        "binance-options-rest" => "https://eapi.binance.com",
        "binance-options-websocket" => "wss://fstream.binance.com/ws",
        "okx-spot-rest" | "okx-swap-rest" | "okx-futures-rest" | "okx-options-rest" => {
            "https://www.okx.com"
        }
        "okx-public-websocket" => "wss://ws.okx.com:8443/ws/v5/public",
        "massive-equity-websocket" => "http://socket.massiveprivateserver.site/stocks",
        "massive-options-websocket" => "http://socket.massiveprivateserver.site/options",
        "hyperliquid-info" => "https://api.hyperliquid.xyz/info",
        "hyperliquid-websocket" => "wss://api.hyperliquid.xyz/ws",
        _ => "",
    }
}

#[cfg(test)]
mod tests {
    use super::default_endpoint;

    #[test]
    fn binance_spot_websocket_uses_a_websocket_endpoint() {
        assert_eq!(
            default_endpoint("binance-spot-websocket"),
            "wss://stream.binance.com:9443/ws"
        );
    }

    #[test]
    fn unknown_endpoint_key_never_falls_back_to_binance() {
        assert_eq!(default_endpoint("future-provider"), "");
    }
}
