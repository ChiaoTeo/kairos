//! Pure backtest result calculations.
//!
//! Market replay and runtime composition stay outside execution. This API
//! accepts normalized equity and fill facts for CLI, server, or system use.

use serde::{Deserialize, Serialize};

use crate::domain::OrderSide;
use crate::services::simulator::{
    ExecutionSimulator, SimulationConfig, SimulationFill, SimulationOrder, SimulationOrderRequest,
};
use kairos_domain_types::{InstrumentId, Money, Price, Quantity, Rate, UnixNanos};
use kairos_market_contract::model::MarketObservation;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct BacktestEquityPoint {
    pub observed_at_unix_nanos: UnixNanos,
    pub equity: Money,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct BacktestFill {
    pub instrument_id: InstrumentId,
    pub side: OrderSide,
    pub quantity: Quantity,
    pub price: Price,
    #[serde(default)]
    pub fee: Money,
    pub occurred_at_unix_nanos: UnixNanos,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct BacktestRequest {
    pub initial_equity: Money,
    #[serde(default)]
    pub equity_curve: Vec<BacktestEquityPoint>,
    #[serde(default)]
    pub fills: Vec<BacktestFill>,
    #[serde(default)]
    pub risk_free_rate: Rate,
    pub annualization_periods: Option<f64>,
    #[serde(default)]
    pub market_events: Vec<MarketObservation>,
    #[serde(default)]
    pub orders: Vec<SimulationOrderRequest>,
    #[serde(default)]
    pub simulation: SimulationConfig,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct BacktestRunResult {
    pub metrics: BacktestMetrics,
    pub orders: Vec<SimulationOrder>,
    pub fills: Vec<SimulationFill>,
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
    /// Runs the deterministic execution part of a backtest.
    ///
    /// Account settlement remains the owner of balances, positions and
    /// equity. This method returns normalized simulation fills so the
    /// composition/application layer can hand them to Account.
    pub fn run(request: BacktestRequest) -> Result<BacktestRunResult, String> {
        let mut simulator = ExecutionSimulator::new(request.simulation.clone())?;
        for order in request.orders.iter().cloned() {
            simulator.submit(order)?;
        }
        for event in request.market_events.iter().cloned() {
            simulator.apply_market_event(event)?;
        }
        let result = simulator.result();
        let fills = result
            .fills
            .iter()
            .map(backtest_fill)
            .collect::<Result<Vec<_>, _>>()?;
        let mut metric_request = request;
        metric_request.fills = fills;
        metric_request.market_events = Vec::new();
        metric_request.orders = Vec::new();
        Ok(BacktestRunResult {
            metrics: Self::evaluate(metric_request)?,
            orders: result.orders,
            fills: result.fills,
        })
    }

    pub fn evaluate(request: BacktestRequest) -> Result<BacktestMetrics, String> {
        let initial_equity = number(&request.initial_equity.to_string(), "initial_equity")?;
        let equity = request
            .equity_curve
            .iter()
            .map(|point| number(&point.equity.to_string(), "equity"))
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
        let risk_free_rate = if request.risk_free_rate.mantissa() == 0 {
            0.0
        } else {
            number(&request.risk_free_rate.to_string(), "risk_free_rate")?
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

fn backtest_fill(fill: &SimulationFill) -> Result<BacktestFill, String> {
    Ok(BacktestFill {
        instrument_id: fill.instrument_id.clone(),
        side: fill.side,
        quantity: fill.quantity,
        price: fill.price,
        fee: fill.fee,
        occurred_at_unix_nanos: fill.occurred_at_unix_nanos,
    })
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
        let quantity = number(&fill.quantity.to_string(), "fill.quantity")?;
        let price = number(&fill.price.to_string(), "fill.price")?;
        let fee = number(&fill.fee.to_string(), "fill.fee")?;
        if quantity <= 0.0 || price <= 0.0 || fee < 0.0 {
            return Err("fill quantity and price must be positive; fee cannot be negative".into());
        }
        match fill.side {
            OrderSide::Buy => match open.get_mut(fill.instrument_id.as_str()) {
                Some(current) => {
                    let total = current.quantity + quantity;
                    current.entry_price =
                        (current.quantity * current.entry_price + quantity * price) / total;
                    current.quantity = total;
                    current.fees += fee;
                }
                None => {
                    open.insert(
                        fill.instrument_id.to_string(),
                        OpenTrade {
                            quantity,
                            entry_price: price,
                            fees: fee,
                        },
                    );
                }
            },
            OrderSide::Sell => {
                let Some(mut current) = open.remove(fill.instrument_id.as_str()) else {
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
                    open.insert(fill.instrument_id.to_string(), current);
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
