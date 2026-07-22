from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

from kairospy.execution.ports import ComboOrderRequest, Environment, OrderRequest
from kairospy.execution.orders import OrderType
from kairospy.execution.events import TradeSide
from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore
from kairospy.infrastructure.storage.codec import to_primitive


@dataclass(frozen=True, slots=True)
class ExecutionCalibrationRelease:
    manifest_path: Path
    manifest: dict[str, object]

    @property
    def release_id(self) -> str:
        return str(self.manifest["release_id"])

    @property
    def release_hash(self) -> str:
        return str(self.manifest["release_hash"])


def load_execution_calibration_release(path: str | Path) -> ExecutionCalibrationRelease:
    manifest_path = Path(path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("kind") != "execution_calibration_release":
        raise ValueError("execution calibration manifest has an unsupported kind")
    if payload.get("schema_version") != 1:
        raise ValueError("execution calibration manifest has an unsupported schema version")
    release_hash = str(payload.get("release_hash", ""))
    release_id = str(payload.get("release_id", ""))
    material = dict(payload)
    material.pop("release_hash", None)
    material.pop("release_id", None)
    actual_hash = _release_hash(material)
    if release_hash != actual_hash:
        raise ValueError("execution calibration manifest hash does not match its content")
    expected_id = f"{payload.get('calibration_id')}-{release_hash[:16]}"
    if release_id != expected_id:
        raise ValueError("execution calibration manifest release_id does not match its hash")
    return ExecutionCalibrationRelease(manifest_path, to_primitive(payload))


def build_execution_calibration_release(
    runtime_db: str | Path, output_root: str | Path, *,
    venue: str, environment: Environment | str, strategy_id: str | None = None,
    calibration_id: str = "execution-calibration-v1",
) -> ExecutionCalibrationRelease:
    store = SQLiteRuntimeStore(runtime_db)
    records = tuple(item for item in store.execution_records()
                    if strategy_id is None or item.order.request.strategy_id == strategy_id)
    if not records:
        raise ValueError("execution calibration requires at least one filled execution")
    samples = tuple(_sample(item) for item in records)
    payload = {
        "schema_version": 1,
        "kind": "execution_calibration_release",
        "calibration_id": calibration_id,
        "venue": venue,
        "environment": Environment(environment).value,
        "strategy_id": strategy_id,
        "runtime_db": str(Path(runtime_db)),
        "sample_count": len(samples),
        "time_range": {
            "start": min(str(item["execution_time"]) for item in samples),
            "end": max(str(item["execution_time"]) for item in samples),
        },
        "summary": _summary(samples),
        "samples": samples,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "limitations": (
            "local or paper/testnet calibration quality depends on the source runtime",
            "market orders do not have limit-price slippage bps",
            "this release does not replace external L4 soak evidence",
        ),
    }
    release_hash = _release_hash(payload)
    payload["release_hash"] = release_hash
    payload["release_id"] = f"{calibration_id}-{release_hash[:16]}"
    target = Path(output_root) / payload["release_id"] / "manifest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    primitive = to_primitive(payload)
    if target.exists() and json.loads(target.read_text(encoding="utf-8")) != primitive:
        raise ValueError("execution calibration release hash refers to conflicting content")
    if not target.exists():
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(primitive, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
        temporary.replace(target)
    return ExecutionCalibrationRelease(target, primitive)


def _release_hash(payload: dict[str, object]) -> str:
    material = json.dumps(to_primitive(payload), ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    return sha256(material).hexdigest()


def _sample(record) -> dict[str, object]:
    request = record.order.request
    execution = record.execution
    submitted_at = record.order.created_at
    accepted_at = record.order.ack.accepted_at if record.order.ack is not None else None
    notional = execution.quantity * execution.price
    limit_price = _limit_price(request)
    side = _side(request, execution.side)
    slippage_bps = None
    if limit_price is not None:
        raw = (execution.price - limit_price) / limit_price * Decimal("10000")
        slippage_bps = raw if side is TradeSide.BUY else -raw
    return {
        "external_key": record.external_key,
        "client_order_id": record.client_order_id,
        "strategy_id": request.strategy_id,
        "instrument_id": execution.instrument_id.value,
        "order_type": request.instructions.order_type.value,
        "time_in_force": request.instructions.time_in_force.value,
        "side": side.value,
        "submitted_at": submitted_at.isoformat(),
        "accepted_at": accepted_at.isoformat() if accepted_at else None,
        "execution_time": execution.timestamp.isoformat(),
        "ack_latency_ms": str(_milliseconds(accepted_at - submitted_at)) if accepted_at else None,
        "fill_latency_ms": str(_milliseconds(execution.timestamp - (accepted_at or submitted_at))),
        "requested_quantity": str(_quantity(request)),
        "filled_quantity": str(execution.quantity),
        "fill_ratio": str(execution.quantity / _quantity(request)),
        "execution_price": str(execution.price),
        "limit_price": str(limit_price) if limit_price is not None else None,
        "slippage_bps": str(slippage_bps) if slippage_bps is not None else None,
        "fee": str(execution.fee),
        "fee_asset": execution.fee_asset.value,
        "fee_bps": str((execution.fee / notional * Decimal("10000")) if notional else Decimal("0")),
        "order_status": record.order.status.value,
    }


def _summary(samples: tuple[dict[str, object], ...]) -> dict[str, object]:
    return {
        "ack_latency_ms": _stats(item["ack_latency_ms"] for item in samples if item["ack_latency_ms"] is not None),
        "fill_latency_ms": _stats(item["fill_latency_ms"] for item in samples),
        "fill_ratio": _stats(item["fill_ratio"] for item in samples),
        "fee_bps": _stats(item["fee_bps"] for item in samples),
        "slippage_bps": _stats(item["slippage_bps"] for item in samples if item["slippage_bps"] is not None),
        "by_order_type": _counts(str(item["order_type"]) for item in samples),
        "by_time_in_force": _counts(str(item["time_in_force"]) for item in samples),
        "by_instrument": _counts(str(item["instrument_id"]) for item in samples),
    }


def _stats(values) -> dict[str, object] | None:
    values = sorted(Decimal(str(value)) for value in values)
    if not values:
        return None
    return {
        "count": len(values),
        "min": str(values[0]),
        "max": str(values[-1]),
        "mean": str(sum(values, Decimal("0")) / Decimal(len(values))),
        "p50": str(values[len(values) // 2]),
    }


def _counts(values) -> dict[str, int]:
    output: dict[str, int] = {}
    for value in values:
        output[value] = output.get(value, 0) + 1
    return output


def _limit_price(request: OrderRequest | ComboOrderRequest) -> Decimal | None:
    if request.instructions.order_type is OrderType.MARKET:
        return None
    return request.instructions.limit_price


def _side(request: OrderRequest | ComboOrderRequest, fallback: TradeSide) -> TradeSide:
    return request.side if isinstance(request, OrderRequest) else fallback


def _quantity(request: OrderRequest | ComboOrderRequest) -> Decimal:
    return request.quantity


def _milliseconds(delta) -> Decimal:
    return Decimal(str(delta.total_seconds() * 1000))
