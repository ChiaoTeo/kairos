pub mod pro;
mod rest;
mod websocket;

pub use rest::BinancePortfolioMarginRestConnection;
pub use websocket::BinancePortfolioMarginUserWebSocketConnection;
