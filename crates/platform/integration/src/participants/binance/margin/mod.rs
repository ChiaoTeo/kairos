mod rest;
mod websocket;

pub use rest::BinanceMarginRestConnection;
pub use websocket::{BinanceMarginUserWebSocketConnection, BinanceMarginWebSocketConnection};
