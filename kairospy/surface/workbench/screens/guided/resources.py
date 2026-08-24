"""Single-input runtime resource query helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from kairospy.investment.apps.account.application import (
    AccountConfigurationApplication,
)
from kairospy.investment.apps.reference.application import (
    ReferenceProviderConfigurationApplication,
)
from kairospy.strategy.apps.agent.application import AgentResourceApplication
from kairospy.strategy.apps.notification.application import (
    NotificationAdminApplication,
)
from kairospy.system.apps.configuration.application import (
    ConfigurationReferenceApplication,
    WorkspaceResourceLifecycleApplication,
)
from kairospy.system.apps.launch.application import (
    LaunchNotificationConfigurationApplication,
)
from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
    SecretRef,
)

from ...widgets import ActionItem
from .resource_rendering import (
    RESOURCE_LABELS,
    detail_renderable,
    identity,
    records_renderable,
    summary_renderable,
)
from .resource_wizard import ResourceWizardState, save_resource_wizard


def detail_actions(kind: str | None) -> tuple[ActionItem, ...]:
    common = (
        ActionItem("test", "测试连接", "执行真实的低成本连接验证", "1"),
        ActionItem("edit", "修改配置", "逐字段更新安全配置", "2"),
        ActionItem("advanced", "安全与高级信息", "查看版本、状态和引用", "3"),
        ActionItem("toggle", "启用或停用", "切换连接可用状态", "4"),
        ActionItem("delete", "删除连接", "移除配置和验证记录", "5"),
    )
    if kind == "accounts":
        return (
            ActionItem("operations", "账户运行查询", "余额、持仓、费率与订单", "1"),
            *tuple(
                ActionItem(item.id, item.label, item.description, str(index))
                for index, item in enumerate(common, 2)
            ),
        )
    if kind == "models":
        return (
            ActionItem("test", "测试模型", "执行最小文本调用", "1"),
            ActionItem("models", "查看已保存模型", "显示当前连接的模型 ID", "2"),
            ActionItem("advanced", "安全与高级信息", "查看版本、状态和引用", "3"),
            ActionItem("edit", "修改配置", "逐字段更新安全配置", "4"),
            ActionItem("toggle", "启用或停用", "切换连接可用状态", "5"),
            ActionItem("delete", "删除连接", "移除配置和验证记录", "6"),
        )
    if kind == "notifications":
        return (
            ActionItem("test", "发送真实测试消息", "验证真实 provider delivery", "1"),
            ActionItem("attach", "绑定到 Launch", "添加通知 route", "2"),
            ActionItem("detach", "从 Launch 解绑", "移除相关 route", "3"),
            ActionItem("validate", "校验通知配置", "检查指定运行模式", "4"),
            ActionItem("advanced", "安全与高级信息", "查看版本、状态和引用", "5"),
            ActionItem("toggle", "启用或停用", "切换通知提醒状态", "6"),
            ActionItem("delete", "删除提醒", "移除配置和验证记录", "7"),
        )
    return common


def summary(state: Any) -> dict[str, tuple[int, int]]:
    groups = {kind: list_records(state, kind) for kind in RESOURCE_LABELS}
    return {
        key: (
            len(records),
            sum(record.get("verification_status") == "verified" for record in records),
        )
        for key, records in groups.items()
    }


def list_records(state: Any, kind: str) -> tuple[dict[str, Any], ...]:
    owner = _owner(state)
    if kind == "accounts":
        records = AccountConfigurationApplication(owner).list()
    elif kind == "data":
        records = ReferenceProviderConfigurationApplication(owner).list()
    elif kind == "models":
        records = AgentResourceApplication(owner).model_connections()
    elif kind == "notifications":
        records = NotificationAdminApplication(owner).list()
    else:
        raise ValueError(f"unknown resource kind: {kind}")
    return tuple(dict(record) for record in records)


def execute_action(
    state: Any,
    kind: str,
    record: dict[str, Any],
    action: str,
    *,
    value: str | None = None,
    launch_id: str | None = None,
) -> Any:
    owner = _owner(state)
    resource_id = identity(kind, record)
    if action == "delete":
        return WorkspaceResourceLifecycleApplication(owner).delete(
            _reference_kind(kind), resource_id
        )
    if kind == "accounts":
        application = AccountConfigurationApplication(owner)
        if action == "test":
            return application.test_connection(resource_id)
        if action == "toggle":
            disabled = str(record.get("status") or "").lower() == "disabled"
            return application.modify(
                resource_id, status="configured" if disabled else "disabled"
            )
        if action == "advanced":
            return _advanced(owner, kind, resource_id, record)
    elif kind == "data":
        application = ReferenceProviderConfigurationApplication(owner)
        if action == "test":
            return application.test_connection(resource_id)
        if action == "toggle":
            return application.set_enabled(
                resource_id, enabled=not bool(record.get("enabled", True))
            )
        if action == "advanced":
            return _advanced(owner, kind, resource_id, record)
    elif kind == "models":
        application = AgentResourceApplication(owner)
        if action == "test":
            return application.test_model_connection(resource_id, value or "")
        if action == "toggle":
            return application.set_model_connection_enabled(
                resource_id, enabled=not bool(record.get("enabled", True))
            )
        if action == "advanced":
            return _advanced(owner, kind, resource_id, record)
    elif kind == "notifications":
        application = NotificationAdminApplication(owner)
        if action == "test":
            return asyncio.run(application.test_destination(resource_id))
        if action == "toggle":
            return application.set_enabled(
                resource_id, not bool(record.get("enabled", True))
            )
        if action == "advanced":
            return _advanced(owner, kind, resource_id, record)
        if action == "validate":
            return application.validate_workspace(mode=value or "paper")
        if action == "attach":
            if not launch_id:
                raise ValueError("通知绑定需要 Launch ID")
            application.show(resource_id)
            return LaunchNotificationConfigurationApplication(owner).attach(
                launch_id, resource_id, route=value or "signals", default=True
            )
        if action == "detach":
            if not launch_id:
                raise ValueError("通知解绑需要 Launch ID")
            return LaunchNotificationConfigurationApplication(owner).detach(
                launch_id, resource_id
            )
    raise ValueError(f"unsupported {kind} resource action: {action}")


def preview_action(
    kind: str,
    record: Mapping[str, Any],
    action: str,
    *,
    value: str | None = None,
    launch_id: str | None = None,
) -> dict[str, Any]:
    return {
        "status": "preview",
        "kind": kind,
        "resource": identity(kind, record),
        "action": action,
        "value": value,
        "launch_id": launch_id,
    }


def _owner(state: Any) -> Any:
    if state.owner is None:
        raise RuntimeError(state.load_error or "当前没有可用的 workspace")
    return state.owner


def _advanced(
    owner: Any, kind: str, resource_id: str, record: Mapping[str, Any]
) -> dict[str, Any]:
    references = ConfigurationReferenceApplication(owner)
    if kind == "accounts":
        uses = references.account_references(resource_id)
    elif kind == "data":
        uses = references.data_provider_references(resource_id)
    elif kind == "notifications":
        uses = references.destination_references(resource_id)
    else:
        uses = references.model_connection_references(resource_id)
    return {
        "identity": resource_id,
        "credential_id": record.get("credential_id"),
        "current_configuration_hash": record.get("current_configuration_hash"),
        "tested_configuration_hash": record.get("tested_configuration_hash"),
        "tested": record.get("tested") or [],
        "not_tested": record.get("not_tested") or [],
        "references": uses,
    }


def _reference_kind(kind: str) -> str:
    return {
        "accounts": "account",
        "data": "market_data",
        "models": "ai_model",
        "notifications": "notification",
    }[kind]


__all__ = [
    "ResourceWizardState",
    "detail_actions",
    "detail_renderable",
    "execute_action",
    "identity",
    "list_records",
    "preview_action",
    "records_renderable",
    "summary",
    "summary_renderable",
    "save_resource_wizard",
]
