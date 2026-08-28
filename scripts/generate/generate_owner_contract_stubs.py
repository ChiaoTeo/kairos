#!/usr/bin/env python3
"""Generate mechanically exact structural stubs from built owner extensions."""

from __future__ import annotations

import argparse
import ast
import importlib
import inspect
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
OWNERS = ("account", "capital", "execution", "market", "reference", "risk")
HAND_TYPED_OWNERS = frozenset({"execution", "market", "risk"})
MODULE_FUNCTIONS = {
    owner: ("build_info", "decode_event", "indexed_environment_path")
    for owner in OWNERS
}
MODULE_FUNCTIONS["reference"] = ("build_info", "decode_event")
RETURN_TYPES = {
    ("account", "AccountControlClient", "advance_time"): "AdvanceAccountTimeResponse",
    ("account", "AccountControlClient", "apply_simulated_capital_mutation"): "AccountCommandStatus",
    ("account", "AccountControlClient", "apply_simulated_settlement"): "AccountCommandStatus",
    ("account", "AccountControlClient", "health"): "AccountHealth",
    ("account", "AccountControlClient", "mark_to_market"): "AccountCommandStatus",
    ("account", "AccountControlClient", "query_simulated_capital_mutation"): "SimulatedCapitalMutationStatusResponse",
    ("account", "AccountControlClient", "reconcile"): "AccountRefreshResponse",
    ("account", "AccountControlClient", "refresh"): "AccountRefreshResponse",
    ("capital", "CapitalControlClient", "cancel_funding_objective"): "CapitalControlResponse",
    ("capital", "CapitalControlClient", "health"): "CapitalHealth",
    ("capital", "CapitalControlClient", "observe_capital_demand"): "CapitalDemandResponse",
    ("capital", "CapitalControlClient", "publish_funding_objective"): "CapitalControlResponse",
    ("capital", "CapitalControlClient", "query_capital_availability"): "CapitalAvailabilityResponse",
    ("capital", "CapitalControlClient", "reconcile_capital_plan"): "ReconcileCapitalPlanResponse",
}
STUB_IMPORTS = {
    "account": (
        "from kairospy.primitives.decimal import MoneyLike, PriceLike, QuantityLike, SignedQuantityLike",
        "from kairospy.primitives.account import AccountIdRead, SegmentKeyRead",
        "from kairospy.primitives.execution import OrderIdRead",
        "from kairospy.primitives.reference import AssetIdRead, InstrumentIdRead, MarketIdRead",
        "from kairospy.primitives.runtime import RequestIdRead",
    ),
    "capital": (
        "from kairospy.primitives.decimal import QuantityLike",
        "from kairospy.primitives.account import AccountIdRead, BrokerIdRead, SegmentKeyRead",
        "from kairospy.primitives.capital import CapitalDemandIdRead, CapitalGroupIdRead, CapitalPlanIdRead, CapitalReservationIdRead, FundingObjectiveIdRead",
        "from kairospy.primitives.runtime import RequestIdRead, StrategyIdRead",
    ),
    "reference": (
        "from kairospy.primitives.decimal import MoneyLike, PriceLike, QuantityLike, RateLike",
        "from kairospy.primitives.reference import AssetIdRead, ExchangeIdRead, InstrumentIdRead, ListingIdRead, MarketIdRead, SymbolRead",
    ),
}
IDENTITY_PROPERTY_TYPES = {
    "account": {
        "account_id": "AccountIdRead",
        "segment_key": "SegmentKeyRead",
        "instrument_id": "InstrumentIdRead",
        "order_id": "OrderIdRead",
        "asset": "AssetIdRead",
        "asset_id": "AssetIdRead",
        "market_id": "MarketIdRead | None",
        "request_id": "RequestIdRead",
    },
    "capital": {
        "account_id": "AccountIdRead",
        "broker": "BrokerIdRead",
        "segment": "SegmentKeyRead",
        "capital_group_id": "CapitalGroupIdRead",
        "objective_id": "FundingObjectiveIdRead",
        "demand_id": "CapitalDemandIdRead",
        "plan_id": "CapitalPlanIdRead",
        "reservation_id": "CapitalReservationIdRead",
        "request_id": "RequestIdRead",
        "strategy_id": "StrategyIdRead",
    },
    "reference": {
        "asset_id": "AssetIdRead",
        "base_asset_id": "AssetIdRead | None",
        "quote_asset_id": "AssetIdRead | None",
        "exchange_id": "ExchangeIdRead",
        "instrument_id": "InstrumentIdRead",
        "underlying_instrument_id": "InstrumentIdRead | None",
        "listing_id": "ListingIdRead | None",
        "market_id": "MarketIdRead | None",
        "symbol": "SymbolRead",
    },
}
PROPERTY_TYPES = {
    ("account", class_name, field): value_type
    for class_name, fields in {
        "AccountBalanceCurrent": {
            "available": "QuantityLike",
            "reserved": "QuantityLike",
            "total": "QuantityLike",
        },
        "AccountBalanceEvent": {
            "available": "QuantityLike",
            "borrowed": "QuantityLike",
            "interest": "QuantityLike",
            "locked": "QuantityLike",
            "total": "QuantityLike",
        },
        "AccountCollateralCurrent": {
            "available": "QuantityLike",
            "borrowed": "QuantityLike",
            "interest": "QuantityLike",
            "locked": "QuantityLike",
            "total": "QuantityLike",
        },
        "AccountEarnHoldingCurrent": {
            "principal": "QuantityLike",
            "redeemable": "QuantityLike",
        },
        "AccountEarnHoldingEvent": {
            "principal": "QuantityLike",
            "redeemable": "QuantityLike",
        },
        "AccountObservedOrderCurrent": {
            "filled_quantity": "QuantityLike",
            "quantity": "QuantityLike",
        },
        "AccountObservedOrderEvent": {
            "filled_quantity": "QuantityLike",
            "quantity": "QuantityLike",
        },
        "AccountPositionCurrent": {
            "average_price": "PriceLike | None",
            "quantity": "SignedQuantityLike",
            "unrealized_pnl": "MoneyLike | None",
        },
        "AccountPositionEvent": {
            "average_price": "PriceLike | None",
            "mark_price": "PriceLike | None",
            "quantity": "SignedQuantityLike",
            "realized_pnl": "MoneyLike",
            "unrealized_pnl": "MoneyLike",
        },
        "AccountSegmentCurrent": {"equity": "MoneyLike"},
        "AccountValuationEvent": {"equity": "MoneyLike"},
    }.items()
    for field, value_type in fields.items()
}
PROPERTY_TYPES.update(
    {
        ("capital", class_name, field): "QuantityLike" + optional
        for class_name, fields in {
            "PublishFundingObjectiveRequest": {"desired_available": ""},
            "ObserveCapitalDemandRequest": {"observed_shortfall": ""},
            "CapitalAvailabilityResponse": {
                "policy_minimum": "",
                "policy_default_target": "",
                "policy_maximum": "",
                "desired_target": "",
                "observed_available": "",
                "effective_target": "",
                "deficit": "",
            },
            "FundingHorizon": {"desired_available": ""},
            "CapitalAvailability": {
                "desired_target": "",
                "observed_available": "",
                "effective_target": "",
                "deficit": "",
            },
            "FundingObjective": {"desired_available": ""},
            "CapitalDemand": {"observed_shortfall": ""},
            "CapitalPolicy": {
                "minimum": "",
                "default_target": "",
                "maximum": "",
                "stress_buffer": "",
                "minimum_movement": "",
                "hysteresis": "",
            },
            "CapitalEarnHolding": {"principal": "", "redeemable_amount": ""},
            "CapitalFacts": {"observed_available": "", "risk_capacity": ""},
            "CapitalPlan": {
                "amount": "",
                "source_observed_available": "",
                "destination_observed_available": "",
                "redemption_observed_available": " | None",
                "earn_principal_before": "",
            },
            "CapitalRoute": {"per_operation_limit": "", "daily_limit": ""},
            "CapitalReservation": {"amount": ""},
        }.items()
        for field, optional in fields.items()
    }
)
PROPERTY_TYPES.update(
    {
        ("reference", "ReferenceInstrument", "strike"): "PriceLike | None",
        ("reference", "ReferenceListing", "listing_id"): "ListingIdRead",
        ("reference", "ReferenceMarket", "price_tick"): "PriceLike | None",
        ("reference", "ReferenceMarket", "quantity_tick"): "QuantityLike | None",
        ("reference", "ReferenceMarket", "minimum_quantity"): "QuantityLike | None",
        ("reference", "ReferenceMarket", "minimum_notional"): "MoneyLike | None",
        ("reference", "ReferenceMarket", "contract_size"): "RateLike | None",
    }
)


def _signature(value: object) -> str:
    try:
        return str(inspect.signature(value))
    except (TypeError, ValueError):
        return "(*args, **kwargs)"


def _classes(module: ModuleType) -> list[tuple[str, type[object]]]:
    result: list[tuple[str, type[object]]] = []
    for name, value in vars(module).items():
        if name.startswith("_") or not inspect.isclass(value):
            continue
        if getattr(value, "__module__", "") == module.__name__ or issubclass(
            value, Exception
        ):
            result.append((name, value))
    return sorted(result)


def _render_class(owner: str, name: str, value: type[object]) -> list[str]:
    base = ""
    if issubclass(value, ValueError):
        base = "(ValueError)"
    elif issubclass(value, RuntimeError):
        base = "(RuntimeError)"
    lines = [f"class {name}{base}:"]
    members: list[str] = []
    if not issubclass(value, Exception):
        signature = _signature(value)
        if signature != "()":
            constructor = (
                f"(self, {signature[1:]}"
                if signature != "()"
                else "(self)"
            )
            members.append(f"    def __init__{constructor} -> None: ...")
        for member_name, member in inspect.getmembers(value):
            if member_name.startswith("_"):
                continue
            if inspect.isgetsetdescriptor(member):
                result = PROPERTY_TYPES.get(
                    (owner, name, member_name),
                    IDENTITY_PROPERTY_TYPES.get(owner, {}).get(member_name, "object"),
                )
                members.extend(
                    (
                        "    @property",
                        f"    def {member_name}(self) -> {result}: ...",
                    )
                )
            elif inspect.ismethoddescriptor(member):
                result = RETURN_TYPES.get((owner, name, member_name), "object")
                members.append(
                    f"    def {member_name}{_signature(member)} -> {result}: ..."
                )
            elif inspect.isbuiltin(member):
                members.extend(
                    (
                        "    @staticmethod",
                        f"    def {member_name}{_signature(member)} -> object: ...",
                    )
                )
    lines.extend(members or ["    ..."])
    return lines


def render(owner: str) -> str:
    module = importlib.import_module(f"kairospy._native_{owner}_contract")
    lines = [
        "# Generated by scripts/generate/generate_owner_contract_stubs.py.",
        "# Do not edit by hand.",
        *STUB_IMPORTS.get(owner, ()),
        "",
    ]
    for name, value in _classes(module):
        lines.extend(_render_class(owner, name, value))
        lines.append("")
    for name in MODULE_FUNCTIONS[owner]:
        value = getattr(module, name)
        lines.append(f"def {name}{_signature(value)} -> object: ...")
    lines.append("")
    for name, value in sorted(vars(module).items()):
        if name.startswith("_") or inspect.isclass(value) or inspect.isroutine(value):
            continue
        if isinstance(value, bool):
            lines.append(f"{name}: bool")
        elif isinstance(value, int):
            lines.append(f"{name}: int")
        elif isinstance(value, str):
            lines.append(f"{name}: str")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("owners", nargs="*", choices=OWNERS, default=list(OWNERS))
    parser.add_argument(
        "--augment",
        action="store_true",
        help="append runtime classes missing from an existing hand-typed stub",
    )
    args = parser.parse_args()
    for owner in args.owners:
        target = ROOT / "kairospy" / f"_native_{owner}_contract.pyi"
        rendered = render(owner)
        if (args.augment or owner in HAND_TYPED_OWNERS) and target.exists():
            source = target.read_text(encoding="utf-8")
            declared = {
                node.name
                for node in ast.parse(source).body
                if isinstance(node, ast.ClassDef)
            }
            module = importlib.import_module(f"kairospy._native_{owner}_contract")
            blocks = [
                "\n".join(_render_class(owner, name, value))
                for name, value in _classes(module)
                if name not in declared
            ]
            if blocks:
                rendered = source.rstrip() + "\n\n" + "\n\n".join(blocks) + "\n"
            else:
                rendered = source
        target.write_text(rendered, encoding="utf-8")
        print(target.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
