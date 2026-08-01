from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from kairospy.application.usecases.strategy.protocol import StrategyBase
from kairospy.core.intent import target_position_intent
from kairospy.core.market import Bar, RateObservation
from kairospy.core.reference import MarketRef


@dataclass(frozen=True, slots=True)
class FundingArbitrageParams:
    symbol: str = "BTC/USDT"
    target_notional: Decimal = Decimal("1000")
    entry_funding_rate: Decimal = Decimal("0.0001")
    exit_funding_rate: Decimal = Decimal("0")
    max_basis: Decimal = Decimal("0.002")
    estimated_round_trip_fee_rate: Decimal = Decimal("0")
    min_net_funding_rate: Decimal = Decimal("0")


@dataclass(slots=True)
class SymbolState:
    spot: MarketRef
    swap: MarketRef
    spot_price: Decimal | None = None
    swap_price: Decimal | None = None
    in_position: bool = False


class BinanceFundingArbitrageStrategy(StrategyBase):
    strategy_id = "binance-funding-arbitrage"

    def __init__(
        self,
        *,
        symbol: str = "BTC/USDT",
        target_notional: str = "1000",
        entry_funding_rate: str = "0.0001",
        exit_funding_rate: str = "0",
        max_basis: str = "0.002",
        estimated_round_trip_fee_rate: str = "0",
        min_net_funding_rate: str = "0",
    ) -> None:
        self.params = FundingArbitrageParams(
            symbol=symbol,
            target_notional=Decimal(target_notional),
            entry_funding_rate=Decimal(entry_funding_rate),
            exit_funding_rate=Decimal(exit_funding_rate),
            max_basis=Decimal(max_basis),
            estimated_round_trip_fee_rate=Decimal(estimated_round_trip_fee_rate),
            min_net_funding_rate=Decimal(min_net_funding_rate),
        )
        self.spot = MarketRef.ephemeral(venue="binance", market="spot", source_symbol=symbol)
        self.swap = MarketRef.ephemeral(venue="binance", market="swap", source_symbol=symbol)
        self.spot_price: Decimal | None = None
        self.swap_price: Decimal | None = None
        self.in_position = False

    def on_start(self, context) -> None:
        context.subscribe(self.spot, selectors=(Bar.select(interval="1m"),), identity=f"{self.strategy_id}-spot")
        context.subscribe(self.swap, selectors=(Bar.select(interval="1m"),), identity=f"{self.strategy_id}-swap")
        context.subscribe(
            self.swap,
            selectors=(RateObservation.select(basis="funding_rate"),),
            identity=f"{self.strategy_id}-funding",
        )

    def on_data(self, context, signal) -> None:
        value = getattr(getattr(signal, "payload", None), "value", None)
        if isinstance(value, Bar):
            self._update_price(value)
            return None
        if not isinstance(value, RateObservation) or value.basis != "funding_rate":
            return None

        price = self.swap_price or value.mark_price or self.spot_price
        if price is None or price <= 0:
            context.trace(
                "funding_arbitrage_decision",
                {
                    "symbol": self.params.symbol,
                    "funding_rate": value.rate,
                    "spot_price": self.spot_price,
                    "swap_price": self.swap_price,
                    "mark_price": value.mark_price,
                    "decision": {"action": "skip", "reason": "missing_price"},
                },
            )
            return None
        basis = self._basis()
        net_funding_rate = value.rate - self.params.estimated_round_trip_fee_rate
        should_enter = (
            value.rate >= self.params.entry_funding_rate
            and net_funding_rate >= self.params.min_net_funding_rate
            and (basis is None or abs(basis) <= self.params.max_basis)
        )
        should_exit = value.rate <= self.params.exit_funding_rate or (basis is not None and abs(basis) > self.params.max_basis)

        action = "hold"
        decision_reason = "no_signal"
        intent_ids: tuple[str, ...] = ()
        if should_enter and not self.in_position:
            quantity = self.params.target_notional / price
            intent_ids = self._target(context, signal.time, spot_quantity=quantity, swap_quantity=-quantity, reason=f"funding_rate={value.rate}")
            self.in_position = True
            action = "enter"
            decision_reason = "funding_rate_above_entry_and_basis_ok"
        elif should_exit and self.in_position:
            intent_ids = self._target(context, signal.time, spot_quantity=Decimal("0"), swap_quantity=Decimal("0"), reason=f"funding_rate={value.rate}")
            self.in_position = False
            action = "exit"
            decision_reason = "funding_rate_below_exit_or_basis_too_wide"
        elif should_enter and self.in_position:
            decision_reason = "entry_signal_already_in_position"
        elif should_exit and not self.in_position:
            decision_reason = "exit_signal_without_position"
        context.trace(
            "funding_arbitrage_decision",
            {
                "symbol": self.params.symbol,
                "funding_rate": value.rate,
                "spot_price": self.spot_price,
                "swap_price": self.swap_price,
                "mark_price": value.mark_price,
                "basis": basis,
                "net_funding_rate": net_funding_rate,
                "in_position": self.in_position,
                "signals": {"should_enter": should_enter, "should_exit": should_exit},
                "rules": {
                    "entry_funding_rate": self.params.entry_funding_rate,
                    "exit_funding_rate": self.params.exit_funding_rate,
                    "max_basis": self.params.max_basis,
                    "estimated_round_trip_fee_rate": self.params.estimated_round_trip_fee_rate,
                    "min_net_funding_rate": self.params.min_net_funding_rate,
                },
                "decision": {"action": action, "reason": decision_reason},
                "intent_ids": intent_ids,
            },
        )
        return None

    def _update_price(self, bar: Bar) -> None:
        if bar.close is None:
            return
        if str(bar.market_id) == str(self.spot.market_id):
            self.spot_price = bar.close
        elif str(bar.market_id) == str(self.swap.market_id):
            self.swap_price = bar.close

    def _basis(self) -> Decimal | None:
        if self.spot_price is None or self.swap_price is None or self.spot_price == 0:
            return None
        return (self.swap_price - self.spot_price) / self.spot_price

    def _target(self, context, at, *, spot_quantity: Decimal, swap_quantity: Decimal, reason: str) -> tuple[str, ...]:
        spot_intent = target_position_intent(
            strategy_id=self.strategy_id,
            instrument_id=self.spot.instrument_id,
            market_id=self.spot.market_id,
            target_quantity=spot_quantity,
            at=at,
            reason=reason,
        )
        swap_intent = target_position_intent(
            strategy_id=self.strategy_id,
            instrument_id=self.swap.instrument_id,
            market_id=self.swap.market_id,
            target_quantity=swap_quantity,
            at=at,
            reason=reason,
        )
        context.intent(spot_intent)
        context.intent(swap_intent)
        return (str(spot_intent.intent_id), str(swap_intent.intent_id))


class BinanceMultiFundingArbitrageStrategy(StrategyBase):
    strategy_id = "binance-multi-funding-arbitrage"

    def __init__(
        self,
        *,
        symbols: list[str] | tuple[str, ...] | None = None,
        target_notional_per_symbol: str = "1000",
        entry_funding_rate: str = "0.0001",
        exit_funding_rate: str = "0",
        max_basis: str = "0.002",
        estimated_round_trip_fee_rate: str = "0",
        min_net_funding_rate: str = "0",
        max_positions: int = 3,
    ) -> None:
        selected = tuple(symbols or ("BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"))
        if not selected:
            raise ValueError("symbols cannot be empty")
        if max_positions < 1:
            raise ValueError("max_positions must be positive")
        self.target_notional = Decimal(target_notional_per_symbol)
        self.entry_funding_rate = Decimal(entry_funding_rate)
        self.exit_funding_rate = Decimal(exit_funding_rate)
        self.max_basis = Decimal(max_basis)
        self.estimated_round_trip_fee_rate = Decimal(estimated_round_trip_fee_rate)
        self.min_net_funding_rate = Decimal(min_net_funding_rate)
        self.max_positions = max_positions
        self.states = {
            symbol: SymbolState(
                spot=MarketRef.ephemeral(venue="binance", market="spot", source_symbol=symbol),
                swap=MarketRef.ephemeral(venue="binance", market="swap", source_symbol=symbol),
            )
            for symbol in selected
        }
        self._spot_by_market = {str(state.spot.market_id): state for state in self.states.values()}
        self._swap_by_market = {str(state.swap.market_id): state for state in self.states.values()}

    def on_start(self, context) -> None:
        for symbol, state in self.states.items():
            identity_symbol = _identity_symbol(symbol)
            context.subscribe(state.spot, selectors=(Bar.select(interval="1m"),), identity=f"{self.strategy_id}-{identity_symbol}-spot")
            context.subscribe(state.swap, selectors=(Bar.select(interval="1m"),), identity=f"{self.strategy_id}-{identity_symbol}-swap")
            context.subscribe(
                state.swap,
                selectors=(RateObservation.select(basis="funding_rate"),),
                identity=f"{self.strategy_id}-{identity_symbol}-funding",
            )

    def on_data(self, context, signal) -> None:
        value = getattr(getattr(signal, "payload", None), "value", None)
        if isinstance(value, Bar):
            state = self._spot_by_market.get(str(value.market_id))
            if state is not None:
                state.spot_price = value.close
                return None
            state = self._swap_by_market.get(str(value.market_id))
            if state is not None:
                state.swap_price = value.close
            return None
        if not isinstance(value, RateObservation) or value.basis != "funding_rate":
            return None
        state = self._swap_by_market.get(str(value.market_id))
        if state is None:
            return None
        price = state.swap_price or value.mark_price or state.spot_price
        if price is None or price <= 0:
            context.trace(
                "funding_arbitrage_decision",
                {
                    "symbol": _symbol_for_state(self.states, state),
                    "funding_rate": value.rate,
                    "spot_price": state.spot_price,
                    "swap_price": state.swap_price,
                    "mark_price": value.mark_price,
                    "decision": {"action": "skip", "reason": "missing_price"},
                },
            )
            return None
        basis = _basis(state.spot_price, state.swap_price)
        net_funding_rate = value.rate - self.estimated_round_trip_fee_rate
        should_enter = (
            value.rate >= self.entry_funding_rate
            and net_funding_rate >= self.min_net_funding_rate
            and (basis is None or abs(basis) <= self.max_basis)
        )
        should_exit = value.rate <= self.exit_funding_rate or (basis is not None and abs(basis) > self.max_basis)
        open_count = sum(1 for item in self.states.values() if item.in_position)
        action = "hold"
        decision_reason = "no_signal"
        intent_ids: tuple[str, ...] = ()
        if should_enter and not state.in_position and open_count < self.max_positions:
            quantity = self.target_notional / price
            intent_ids = _target_pair(context, self.strategy_id, signal.time, state=state, spot_quantity=quantity, swap_quantity=-quantity, reason=f"funding_rate={value.rate}")
            state.in_position = True
            action = "enter"
            decision_reason = "funding_rate_above_entry_and_basis_ok"
        elif should_exit and state.in_position:
            intent_ids = _target_pair(context, self.strategy_id, signal.time, state=state, spot_quantity=Decimal("0"), swap_quantity=Decimal("0"), reason=f"funding_rate={value.rate}")
            state.in_position = False
            action = "exit"
            decision_reason = "funding_rate_below_exit_or_basis_too_wide"
        elif should_enter and state.in_position:
            decision_reason = "entry_signal_already_in_position"
        elif should_enter and open_count >= self.max_positions:
            decision_reason = "entry_signal_blocked_by_max_positions"
        elif should_exit and not state.in_position:
            decision_reason = "exit_signal_without_position"
        context.trace(
            "funding_arbitrage_decision",
            {
                "symbol": _symbol_for_state(self.states, state),
                "funding_rate": value.rate,
                "spot_price": state.spot_price,
                "swap_price": state.swap_price,
                "mark_price": value.mark_price,
                "basis": basis,
                "net_funding_rate": net_funding_rate,
                "in_position": state.in_position,
                "open_count": open_count,
                "max_positions": self.max_positions,
                "signals": {"should_enter": should_enter, "should_exit": should_exit},
                "rules": {
                    "entry_funding_rate": self.entry_funding_rate,
                    "exit_funding_rate": self.exit_funding_rate,
                    "max_basis": self.max_basis,
                    "estimated_round_trip_fee_rate": self.estimated_round_trip_fee_rate,
                    "min_net_funding_rate": self.min_net_funding_rate,
                },
                "decision": {"action": action, "reason": decision_reason},
                "intent_ids": intent_ids,
            },
        )
        return None


def _basis(spot_price: Decimal | None, swap_price: Decimal | None) -> Decimal | None:
    if spot_price is None or swap_price is None or spot_price == 0:
        return None
    return (swap_price - spot_price) / spot_price


def _target_pair(
    context,
    strategy_id: str,
    at,
    *,
    state: SymbolState,
    spot_quantity: Decimal,
    swap_quantity: Decimal,
    reason: str,
) -> tuple[str, ...]:
    spot_intent = target_position_intent(
        strategy_id=strategy_id,
        instrument_id=state.spot.instrument_id,
        market_id=state.spot.market_id,
        target_quantity=spot_quantity,
        at=at,
        reason=reason,
    )
    swap_intent = target_position_intent(
        strategy_id=strategy_id,
        instrument_id=state.swap.instrument_id,
        market_id=state.swap.market_id,
        target_quantity=swap_quantity,
        at=at,
        reason=reason,
    )
    context.intent(spot_intent)
    context.intent(swap_intent)
    return (str(spot_intent.intent_id), str(swap_intent.intent_id))


def _symbol_for_state(states: dict[str, SymbolState], target: SymbolState) -> str:
    for symbol, state in states.items():
        if state is target:
            return symbol
    return ""


def _identity_symbol(symbol: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in symbol.lower()).strip("_")


def strategy(
    symbol: str = "BTC/USDT",
    target_notional: str = "1000",
    entry_funding_rate: str = "0.0001",
    exit_funding_rate: str = "0",
    max_basis: str = "0.002",
    estimated_round_trip_fee_rate: str = "0",
    min_net_funding_rate: str = "0",
) -> BinanceFundingArbitrageStrategy:
    return BinanceFundingArbitrageStrategy(
        symbol=symbol,
        target_notional=target_notional,
        entry_funding_rate=entry_funding_rate,
        exit_funding_rate=exit_funding_rate,
        max_basis=max_basis,
        estimated_round_trip_fee_rate=estimated_round_trip_fee_rate,
        min_net_funding_rate=min_net_funding_rate,
    )


def multi_strategy(
    symbols: list[str] | tuple[str, ...] | None = None,
    target_notional_per_symbol: str = "1000",
    entry_funding_rate: str = "0.0001",
    exit_funding_rate: str = "0",
    max_basis: str = "0.002",
    estimated_round_trip_fee_rate: str = "0",
    min_net_funding_rate: str = "0",
    max_positions: int = 3,
) -> BinanceMultiFundingArbitrageStrategy:
    return BinanceMultiFundingArbitrageStrategy(
        symbols=symbols,
        target_notional_per_symbol=target_notional_per_symbol,
        entry_funding_rate=entry_funding_rate,
        exit_funding_rate=exit_funding_rate,
        max_basis=max_basis,
        estimated_round_trip_fee_rate=estimated_round_trip_fee_rate,
        min_net_funding_rate=min_net_funding_rate,
        max_positions=max_positions,
    )
