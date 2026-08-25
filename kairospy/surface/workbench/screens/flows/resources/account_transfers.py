"""Standalone Capital transfer workflow entered from an Account resource."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
from uuid import uuid4

from kairospy.investment.apps.account.application.cli import AccountCliApplication
from kairospy.system.apps.components.application import NativeCliApplication

from ....widgets import ActionItem


TRANSFER_RESULT_ACTIONS = (
    ActionItem("status", "查询本次状态", "安全查询原划转，不重复提交", "1"),
    ActionItem("history", "划转历史", "查看 Capital 本地幂等记录", "2"),
    ActionItem("again", "发起新的划转", "重新输入并获取新预览", "3"),
)


@dataclass(slots=True)
class TransferPromptState:
    account: dict[str, Any]
    values: dict[str, str] = field(default_factory=dict)
    idempotency_key: str = field(
        default_factory=lambda: f"workbench-transfer-{uuid4().hex}"
    )
    preview: dict[str, Any] | None = None

    @property
    def source_account_id(self) -> str:
        return str(self.account.get("account_id") or "").strip()

    @property
    def source_segments(self) -> tuple[str, ...]:
        return tuple(str(value) for value in self.account.get("segments") or ())

    @property
    def source_segment(self) -> str | None:
        selected = self.values.get("source-segment")
        if selected:
            return selected
        return self.source_segments[0] if len(self.source_segments) == 1 else None

    @property
    def destination_account_id(self) -> str:
        return self.values.get("destination-account", self.source_account_id)

    @property
    def destination_segment(self) -> str:
        return self.values.get("destination-segment", "")

    def next_prompt(self) -> tuple[str, str, str] | None:
        for name, label, default in self._steps():
            if name not in self.values:
                detail = (
                    f"直接回车使用 {default}；输入 /back 取消。"
                    if default
                    else "输入 /back 取消。"
                )
                return name, label, detail
        return None

    def accept(self, name: str, raw: str) -> None:
        default = next(
            default for field_name, _label, default in self._steps()
            if field_name == name
        )
        value = raw.strip() or default
        if name == "source-segment" and value not in self.source_segments:
            raise ValueError(
                f"转出分区必须是以下之一：{'、'.join(self.source_segments)}"
            )
        if name in {
            "source-segment",
            "destination-account",
            "destination-segment",
            "asset",
            "amount",
        } and not value:
            raise ValueError(f"{label_for(name)}不能为空")
        if name == "asset":
            value = value.upper()
        if name == "amount":
            try:
                amount = Decimal(value)
            except InvalidOperation as error:
                raise ValueError("划转金额必须是精确十进制数") from error
            if not amount.is_finite() or amount <= 0:
                raise ValueError("划转金额必须大于 0")
            value = format(amount, "f")
        self.values[name] = value
        self.preview = None

    def summary(self) -> dict[str, Any]:
        return {
            "scope": "direct-provider",
            "environment": self.account.get("environment") or "unknown",
            "source_account_id": self.source_account_id,
            "source_segment": self.source_segment or "待选择",
            "destination_account_id": self.destination_account_id,
            "destination_segment": self.destination_segment or "待输入",
            "asset": self.values.get("asset", "待输入"),
            "amount": self.values.get("amount", "待输入"),
        }

    def _steps(self) -> tuple[tuple[str, str, str], ...]:
        source: tuple[tuple[str, str, str], ...] = ()
        if len(self.source_segments) != 1:
            source = ((
                "source-segment",
                f"转出分区（{' / '.join(self.source_segments)}）",
                "",
            ),)
        return (
            *source,
            ("destination-account", "转入账户", self.source_account_id),
            ("destination-segment", "转入分区", ""),
            ("asset", "划转资产", "USDT"),
            ("amount", "划转金额", ""),
        )


def transfer_available(record: Mapping[str, Any]) -> bool:
    capabilities = record.get("capabilities")
    if isinstance(capabilities, list):
        if "transfer" in {str(value) for value in capabilities}:
            return True
    elif str(record.get("credential_role") or "readonly").lower() in {
        "transfer",
        "admin",
    }:
        return True
    return bool(str(record.get("capital_controller_account_id") or "").strip())


def unavailable_result(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "account_id": str(record.get("account_id") or ""),
        "capability": "transfer",
        "available": False,
        "status": "not-authorized",
        "message": "当前账户凭据仅允许读取，不具备资金划转权限。",
        "next_actions": ("查看账户权限", "更换交易账户", "返回"),
    }


def preview(state: Any, prompt: TransferPromptState) -> dict[str, Any]:
    binding = _binding(state, prompt)
    return _capital(
        state,
        (
            "standalone",
            "transfer",
            "preview",
            "--binding-json",
            json.dumps(binding),
            "--amount",
            prompt.values["amount"],
            "--idempotency-key",
            prompt.idempotency_key,
        ),
    )


def confirm(state: Any, prompt: TransferPromptState) -> dict[str, Any]:
    if prompt.preview is None:
        raise ValueError("资金划转预览已经失效，请重新预览")
    binding = _binding(state, prompt)
    return _capital(
        state,
        (
            "standalone",
            "transfer",
            "confirm",
            "--binding-json",
            json.dumps(binding),
            "--preview-json",
            json.dumps(prompt.preview),
            "--confirm-live",
        ),
    )


def status(state: Any, prompt: TransferPromptState, plan_id: str) -> dict[str, Any]:
    return _capital(
        state,
        (
            "standalone",
            "transfer",
            "status",
            "--binding-json",
            json.dumps(_binding(state, prompt)),
            "--plan-id",
            plan_id,
        ),
    )


def history(state: Any, prompt: TransferPromptState) -> dict[str, Any]:
    return _capital(
        state,
        (
            "standalone",
            "transfer",
            "history",
            "--binding-json",
            json.dumps(_binding(state, prompt)),
        ),
    )


def _binding(state: Any, prompt: TransferPromptState) -> dict[str, Any]:
    if state.owner is None:
        raise RuntimeError(state.load_error or "当前没有可用的 Workspace")
    source = _segment_binding(
        state,
        prompt.source_account_id,
        prompt.source_segment or "",
        access="read",
    )
    destination = _segment_binding(
        state,
        prompt.destination_account_id,
        prompt.destination_segment,
        access="read",
    )
    controller: dict[str, Any] | None = None
    if prompt.source_account_id == prompt.destination_account_id:
        source = _segment_binding(
            state,
            prompt.source_account_id,
            prompt.source_segment or "",
            access="transfer",
        )
    else:
        controller_id = str(
            source.get("capital_controller_account_id")
            or destination.get("capital_controller_account_id")
            or ""
        ).strip()
        if not controller_id:
            raise ValueError("跨账户划转缺少 Capital controller 配置")
        if controller_id == prompt.source_account_id:
            controller_segment = prompt.source_segment or ""
        elif controller_id == prompt.destination_account_id:
            controller_segment = prompt.destination_segment
        else:
            controller_segment = _first_account_segment(state, controller_id)
        controller = _segment_binding(
            state, controller_id, controller_segment, access="transfer"
        )
    return {
        "source": source,
        "destination": destination,
        "controller": controller,
        "asset": prompt.values["asset"],
    }


def _segment_binding(
    state: Any, account_id: str, segment: str, *, access: str
) -> dict[str, Any]:
    value = AccountCliApplication(state.owner).run(
        (
            "trading-binding",
            "--account-id",
            account_id,
            "--segment",
            segment,
            "--access",
            access,
        )
    )
    if not isinstance(value, Mapping):
        raise RuntimeError("Account transfer binding 返回了无效结果")
    return {
        "account_id": str(value.get("account_id") or ""),
        "remote_account_id": str(value.get("remote_account_id") or ""),
        "broker": str(value.get("broker") or ""),
        "provider": str(value.get("integration_adapter") or ""),
        "environment": str(value.get("environment") or ""),
        "segment_key": str(value.get("segment_key") or ""),
        "provider_segment": str(value.get("provider_segment") or ""),
        "credential_id": value.get("credential_id"),
        "credential_role": str(value.get("credential_role") or ""),
        "base_url": str(value.get("base_url") or ""),
        "capital_controller_account_id": value.get(
            "capital_controller_account_id"
        ),
        "participant_account_ref": value.get("participant_account_ref"),
    }


def _first_account_segment(state: Any, account_id: str) -> str:
    value = AccountCliApplication(state.owner).run(("show", "--account-id", account_id))
    if not isinstance(value, Mapping):
        raise RuntimeError("Capital controller Account 返回了无效配置")
    segments = value.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError(f"Capital controller Account '{account_id}' 没有可用分区")
    return str(segments[0])


def _capital(state: Any, arguments: tuple[str, ...]) -> dict[str, Any]:
    if state.owner is None:
        raise RuntimeError(state.load_error or "当前没有可用的 Workspace")
    value = NativeCliApplication(state.owner).run("capital", list(arguments))
    if not isinstance(value, dict):
        raise RuntimeError("Capital CLI 返回了无效结果")
    return value


def label_for(name: str) -> str:
    return {
        "source-segment": "转出分区",
        "destination-account": "转入账户",
        "destination-segment": "转入分区",
        "asset": "划转资产",
        "amount": "划转金额",
    }.get(name, name)


__all__ = [
    "TransferPromptState",
    "TRANSFER_RESULT_ACTIONS",
    "confirm",
    "history",
    "preview",
    "status",
    "transfer_available",
    "unavailable_result",
]
