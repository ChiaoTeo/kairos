#!/usr/bin/env python3
"""Verify owner extension classes and declared stub signatures mechanically."""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
OWNERS = ("account", "capital", "execution", "market", "reference", "risk")
MODULE_FUNCTIONS = {
    owner: ("build_info", "decode_event", "indexed_environment_path")
    for owner in OWNERS
}
MODULE_FUNCTIONS["reference"] = ("build_info", "decode_event")
SEMANTIC_PROPERTIES = {
    "reference": {
        ("ReferenceInstrument", "strike"),
        ("ReferenceMarket", "contract_size"),
        ("ReferenceMarket", "minimum_notional"),
        ("ReferenceMarket", "minimum_quantity"),
        ("ReferenceMarket", "price_tick"),
        ("ReferenceMarket", "quantity_tick"),
    },
    "risk": {
        ("RiskAllocation", "amount"),
        ("RiskPolicy", "limit"),
        ("RiskLimitUsage", "available"),
        ("RiskLimitUsage", "reserved"),
        ("RiskLimitUsage", "used"),
    },
}
CANONICAL_IDENTITY_PROPERTIES = {
    "account_id",
    "asset_id",
    "capital_group_id",
    "demand_id",
    "exchange_id",
    "execution_route_id",
    "fill_id",
    "instrument_id",
    "intent_id",
    "leg_id",
    "listing_id",
    "market_id",
    "objective_id",
    "order_id",
    "reservation_id",
    "segment_key",
    "strategy_id",
}


def _runtime_classes(module: object) -> dict[str, type[object]]:
    result: dict[str, type[object]] = {}
    for name, value in vars(module).items():
        if name.startswith("_") or not inspect.isclass(value):
            continue
        if getattr(value, "__module__", "") == module.__name__ or issubclass(
            value, Exception
        ):
            result[name] = value
    return result


def _stub_parameters(arguments: ast.arguments) -> tuple[tuple[str, str, bool], ...]:
    positional = [*arguments.posonlyargs, *arguments.args]
    required = len(positional) - len(arguments.defaults)
    values: list[tuple[str, str, bool]] = []
    for index, argument in enumerate(arguments.posonlyargs):
        values.append((argument.arg, "POSITIONAL_ONLY", index >= required))
    offset = len(arguments.posonlyargs)
    for index, argument in enumerate(arguments.args, start=offset):
        values.append((argument.arg, "POSITIONAL_OR_KEYWORD", index >= required))
    if arguments.vararg is not None:
        values.append((arguments.vararg.arg, "VAR_POSITIONAL", False))
    for argument, default in zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True):
        values.append((argument.arg, "KEYWORD_ONLY", default is not None))
    if arguments.kwarg is not None:
        values.append((arguments.kwarg.arg, "VAR_KEYWORD", False))
    if values and values[0][0] in {"self", "cls"}:
        values.pop(0)
    return tuple(values)


def _runtime_parameters(value: object) -> tuple[tuple[str, str, bool], ...]:
    parameters = []
    for parameter in inspect.signature(value).parameters.values():
        if parameter.name in {"self", "cls"}:
            continue
        parameters.append(
            (
                parameter.name,
                parameter.kind.name,
                parameter.default is not inspect.Parameter.empty,
            )
        )
    return tuple(parameters)


def main() -> int:
    failures: list[str] = []
    for owner in OWNERS:
        module = importlib.import_module(f"kairospy._native_{owner}_contract")
        stub_path = ROOT / "kairospy" / f"_native_{owner}_contract.pyi"
        tree = ast.parse(stub_path.read_text(encoding="utf-8"), filename=str(stub_path))
        classes = _runtime_classes(module)
        stubs = {
            node.name: node for node in tree.body if isinstance(node, ast.ClassDef)
        }
        if "NativeDecimal" in stubs or hasattr(module, "NativeDecimal"):
            failures.append(
                f"{owner}: generic NativeDecimal must not be part of the public contract surface"
            )
        source_path = ROOT / "crates" / "modules" / owner / "contract" / "py" / "src" / "lib.rs"
        if source_path.exists() and "add_class::<NativeDecimal>" in source_path.read_text(
            encoding="utf-8"
        ):
            failures.append(f"{owner}: PyO3 registers generic NativeDecimal")
        if classes.keys() != stubs.keys():
            failures.append(
                f"{owner}: class surface differs; missing={sorted(classes.keys() - stubs.keys())}, "
                f"extra={sorted(stubs.keys() - classes.keys())}"
            )
            continue
        for class_name, node in stubs.items():
            runtime_class = classes[class_name]
            if not issubclass(runtime_class, Exception):
                runtime_members = {
                    name
                    for name, value in inspect.getmembers(runtime_class)
                    if not name.startswith("_")
                    and (
                        inspect.isgetsetdescriptor(value)
                        or inspect.ismethoddescriptor(value)
                        or inspect.isbuiltin(value)
                    )
                }
                stub_members = {
                    member.name
                    for member in node.body
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and member.name != "__init__"
                }
                if runtime_members != stub_members:
                    failures.append(
                        f"{owner}.{class_name}: member surface differs; "
                        f"missing={sorted(runtime_members - stub_members)}, "
                        f"extra={sorted(stub_members - runtime_members)}"
                    )
            for member in node.body:
                if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                is_property = any(
                    isinstance(decorator, ast.Name) and decorator.id == "property"
                    for decorator in member.decorator_list
                )
                annotation = (
                    "" if member.returns is None else ast.unparse(member.returns)
                )
                if (
                    is_property
                    and member.name in CANONICAL_IDENTITY_PROPERTIES
                    and annotation in {"", "object", "str", "str | None"}
                ):
                    failures.append(
                        f"{owner}.{class_name}.{member.name}: canonical identity "
                        f"must use its read type, found {annotation or 'missing'}"
                    )
                if (class_name, member.name) in SEMANTIC_PROPERTIES.get(owner, set()):
                    if annotation in {"", "object", "str", "Decimal"}:
                        failures.append(
                            f"{owner}.{class_name}.{member.name}: semantic value "
                            f"must use a canonical protocol, found {annotation or 'missing'}"
                        )
                if member.name == "__init__":
                    runtime_member: object = runtime_class
                else:
                    runtime_member = inspect.getattr_static(runtime_class, member.name, None)
                    if runtime_member is None:
                        failures.append(f"{owner}.{class_name}.{member.name}: absent at runtime")
                        continue
                    if is_property:
                        if not inspect.isgetsetdescriptor(runtime_member):
                            failures.append(
                                f"{owner}.{class_name}.{member.name}: stub property is not a runtime property"
                            )
                        continue
                    runtime_member = getattr(runtime_class, member.name)
                try:
                    expected = _runtime_parameters(runtime_member)
                except (TypeError, ValueError):
                    continue
                actual = _stub_parameters(member.args)
                if actual != expected:
                    failures.append(
                        f"{owner}.{class_name}.{member.name}: signature {actual!r} != {expected!r}"
                    )
        for function_name in MODULE_FUNCTIONS[owner]:
            if not any(
                isinstance(node, ast.FunctionDef) and node.name == function_name
                for node in tree.body
            ):
                failures.append(f"{owner}: stub omitted {function_name}()")
    if failures:
        print("Owner contract stub violations:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("Owner contract stubs match the loaded extension surfaces.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
