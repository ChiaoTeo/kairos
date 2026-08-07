//! Pure backtest result calculations.
//!
//! Market replay and runtime composition stay outside execution. This API
//! accepts normalized equity and fill facts for CLI, server, or system use.

use serde::{Deserialize, Serialize};

use crate::domain::OrderSide;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct BacktestEquityPoint {
    pub observed_at_unix_nanos: u64,
    pub equity: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct BacktestFill {
    pub instrument_id: String,
    pub side: OrderSide,
    pub quantity: String,
    pub price: String,
    #[serde(default = "zero_string")]
    pub fee: String,
    pub occurred_at_unix_nanos: u64,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct BacktestRequest {
    pub initial_equity: String,
    #[serde(default)]
    pub equity_curve: Vec<BacktestEquityPoint>,
    #[serde(default)]
    pub fills: Vec<BacktestFill>,
    #[serde(default = "zero_string")]
    pub risk_free_rate: String,
    pub annualization_periods: Option<f64>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct BacktestMetrics {
    pub trade_count: usize,
    pub win_count: usize,
    pub loss_count: usize,
    pub win_rate: String,
    pub gross_profit: String,
    pub gross_loss: String,
    pub net_profit: String,
    pub max_drawdown: String,
    pub max_drawdown_pct: String,
    pub sharpe: String,
}

pub struct BacktestApplication;

impl BacktestApplication {
    pub fn evaluate(request: BacktestRequest) -> Result<BacktestMetrics, String> {
        let initial_equity = number(&request.initial_equity, "initial_equity")?;
        let equity = request
            .equity_curve
            .iter()
            .map(|point| number(&point.equity, "equity"))
            .collect::<Result<Vec<_>, _>>()?;
        let trades = closed_trades(&request.fills)?;
        let gross_profit = trades
            .iter()
            .map(|trade| trade.gross_pnl.max(0.0))
            .sum::<f64>();
        let gross_loss = trades
            .iter()
            .map(|trade| trade.gross_pnl.min(0.0))
            .sum::<f64>();
        let net_profit = equity.last().copied().unwrap_or(initial_equity) - initial_equity;
        let win_count = trades.iter().filter(|trade| trade.net_pnl > 0.0).count();
        let loss_count = trades.iter().filter(|trade| trade.net_pnl < 0.0).count();
        let max_drawdown = max_drawdown(&equity);
        let max_equity = equity
            .iter()
            .fold(f64::NEG_INFINITY, |peak, value| peak.max(*value));
        let max_drawdown_pct = if max_equity > 0.0 {
            max_drawdown / max_equity
        } else {
            0.0
        };
        let risk_free_rate = if request.risk_free_rate.trim().is_empty() {
            0.0
        } else {
            number(&request.risk_free_rate, "risk_free_rate")?
        };
        let sharpe = sharpe(&equity, risk_free_rate, request.annualization_periods);
        Ok(BacktestMetrics {
            trade_count: trades.len(),
            win_count,
            loss_count,
            win_rate: format_number(if trades.is_empty() {
                0.0
            } else {
                win_count as f64 / trades.len() as f64
            }),
            gross_profit: format_number(gross_profit),
            gross_loss: format_number(gross_loss),
            net_profit: format_number(net_profit),
            max_drawdown: format_number(max_drawdown),
            max_drawdown_pct: format_number(max_drawdown_pct),
            sharpe: format_number(sharpe),
        })
    }
}

#[derive(Clone, Copy)]
struct OpenTrade {
    quantity: f64,
    entry_price: f64,
    fees: f64,
}

struct ClosedTrade {
    gross_pnl: f64,
    net_pnl: f64,
}

fn closed_trades(fills: &[BacktestFill]) -> Result<Vec<ClosedTrade>, String> {
    let mut open: std::collections::BTreeMap<String, OpenTrade> = std::collections::BTreeMap::new();
    let mut trades = Vec::new();
    for fill in fills {
        let quantity = number(&fill.quantity, "fill.quantity")?;
        let price = number(&fill.price, "fill.price")?;
        let fee = number(&fill.fee, "fill.fee")?;
        if quantity <= 0.0 || price <= 0.0 || fee < 0.0 {
            return Err("fill quantity and price must be positive; fee cannot be negative".into());
        }
        match fill.side {
            OrderSide::Buy => match open.get_mut(&fill.instrument_id) {
                Some(current) => {
                    let total = current.quantity + quantity;
                    current.entry_price =
                        (current.quantity * current.entry_price + quantity * price) / total;
                    current.quantity = total;
                    current.fees += fee;
                }
                None => {
                    open.insert(
                        fill.instrument_id.clone(),
                        OpenTrade {
                            quantity,
                            entry_price: price,
                            fees: fee,
                        },
                    );
                }
            },
            OrderSide::Sell => {
                let Some(mut current) = open.remove(&fill.instrument_id) else {
                    continue;
                };
                let close_quantity = quantity.min(current.quantity);
                let opening_fee = current.fees * close_quantity / current.quantity;
                let closing_fee = fee * close_quantity / quantity;
                let gross_pnl = (price - current.entry_price) * close_quantity;
                trades.push(ClosedTrade {
                    gross_pnl,
                    net_pnl: gross_pnl - opening_fee - closing_fee,
                });
                current.quantity -= close_quantity;
                current.fees -= opening_fee;
                if current.quantity > 0.0 {
                    open.insert(fill.instrument_id.clone(), current);
                }
            }
        }
    }
    Ok(trades)
}

fn max_drawdown(equity: &[f64]) -> f64 {
    let mut peak = f64::NEG_INFINITY;
    let mut result: f64 = 0.0;
    for value in equity {
        peak = peak.max(*value);
        result = result.max(peak - value);
    }
    result
}

fn sharpe(equity: &[f64], risk_free_rate: f64, annualization_periods: Option<f64>) -> f64 {
    let returns: Vec<f64> = equity
        .windows(2)
        .filter_map(|pair| (pair[0] != 0.0).then_some((pair[1] - pair[0]) / pair[0]))
        .collect();
    if returns.len() < 2 {
        return 0.0;
    }
    let excess: Vec<f64> = returns.iter().map(|value| value - risk_free_rate).collect();
    let mean = excess.iter().sum::<f64>() / excess.len() as f64;
    let variance = excess
        .iter()
        .map(|value| (value - mean).powi(2))
        .sum::<f64>()
        / (excess.len() - 1) as f64;
    if variance == 0.0 {
        return 0.0;
    }
    let value = mean / variance.sqrt();
    annualization_periods.map_or(value, |periods| value * periods.max(0.0).sqrt())
}

fn number(value: &str, field: &str) -> Result<f64, String> {
    value
        .trim()
        .parse::<f64>()
        .map_err(|error| format!("{field} must be decimal-compatible: {error}"))
}

fn format_number(value: f64) -> String {
    let value = if value.abs() < 0.0000000000005 {
        0.0
    } else {
        value
    };
    let mut text = format!("{value:.12}");
    while text.contains('.') && text.ends_with('0') {
        text.pop();
    }
    if text.ends_with('.') {
        text.pop();
    }
    text
}

fn zero_string() -> String {
    "0".into()
}
