"""Resource query actions for the Workbench product slice."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from kairospy.investment.apps.account.application import (
    AccountConfigurationApplication,
)
from kairospy.investment.apps.market.application import MarketProviderBindingApplication
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
from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
)
from kairospy.system.apps.integration.application import (
    ProviderConnectionConfigurationApplication,
)

from ....widgets import ActionItem
from .views import (
    RESOURCE_LABELS,
    detail_renderable,
    identity,
    records_renderable,
    summary_renderable,
)
from .wizard import ResourceWizardState, save_resource_wizard


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
            ActionItem(
                "access",
                "管理账户访问",
                "增加或替换 account-read / order-trade binding",
                "2",
            ),
            *tuple(
                ActionItem(item.id, item.label, item.description, str(index))
                for index, item in enumerate(common, 3)
            ),
        )
    if kind == "models":
        return (
            ActionItem("test", "验证并对话", "发送消息；成功回复后标记为已验证", "1"),
            ActionItem("delete", "删除", "移除模型配置和验证记录", "2"),
            ActionItem("edit", "修改", "更新模型服务或服务商模型 ID", "3"),
        )
    if kind == "model_endpoints":
        return (
            ActionItem("discover", "发现模型", "读取服务提供的模型目录", "1"),
            ActionItem("advanced", "安全与高级信息", "查看 Endpoint 和引用", "2"),
            ActionItem("edit", "修改 Endpoint", "更新服务地址、协议和凭据", "3"),
            ActionItem("toggle", "启用或停用", "切换 Endpoint 可用状态", "4"),
            ActionItem("delete", "删除 Endpoint", "仅无模型引用时允许", "5"),
        )
    if kind == "notifications":
        return (
            ActionItem("test", "发送真实测试消息", "验证真实 provider delivery", "1"),
            ActionItem("edit", "修改配置", "更新渠道、安全凭据和目标", "2"),
            ActionItem("toggle", "启用或停用", "切换通知提醒状态", "3"),
            ActionItem(
                "advanced", "使用情况与高级信息", "查看 Launch 引用、版本和状态", "4"
            ),
            ActionItem("delete", "删除提醒", "移除配置和验证记录", "5"),
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
        records = ProviderConnectionConfigurationApplication(owner).list()
    elif kind == "models":
        records = AgentResourceApplication(owner).available_models()
    elif kind == "model_endpoints":
        records = AgentResourceApplication(owner).model_endpoints()
    elif kind == "notifications":
        records = NotificationAdminApplication(owner).list()
    else:
        raise ValueError(f"unknown resource kind: {kind}")
    return tuple(dict(record) for record in records)


def execute_action(
    state: Any,
    kind: str,
    record: Mapping[str, Any],
    action: str,
    *,
    value: str | None = None,
) -> Any:
    owner = _owner(state)
    resource_id = identity(kind, record)
    if action == "delete":
        if kind == "data":
            connections = ProviderConnectionConfigurationApplication(owner)
            external_references = [
                reference
                for reference in ConfigurationReferenceApplication(
                    owner
                ).data_provider_references(resource_id)
                if reference.get("source") != owner.paths.manifest.name
            ]
            if external_references:
                raise ValueError("Provider Connection 仍被 Launch 引用，不能删除")
            MarketProviderBindingApplication(owner).unbind_connection(resource_id)
            if resource_id == "massive":
                return ReferenceProviderConfigurationApplication(owner).delete(
                    resource_id
                )
            return connections.delete(resource_id)
        return WorkspaceResourceLifecycleApplication(owner).delete(
            _reference_kind(kind), resource_id
        )
    if kind == "accounts":
        application = AccountConfigurationApplication(owner)
        if action == "access":
            purpose, separator, credential_id = (value or "").partition("|")
            if not separator:
                raise ValueError("账户访问配置缺少 purpose 或 credential")
            return application.configure_access(
                resource_id,
                purpose=purpose,
                credential_id=credential_id,
                force=True,
            )
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
        application = ProviderConnectionConfigurationApplication(owner)
        if action == "test":
            if resource_id == "massive":
                return ReferenceProviderConfigurationApplication(owner).test_connection(
                    resource_id
                )
            return application.test_connection(resource_id)
        if action == "toggle":
            enabled = not bool(record.get("enabled", True))
            result = application.set_enabled(resource_id, enabled=enabled)
            products = result.get("products")
            for product in products if isinstance(products, list) else ():
                if product != "reference":
                    MarketProviderBindingApplication(owner).bind_connection(
                        resource_id, product=str(product), enabled=enabled
                    )
            return result
        if action == "advanced":
            return _advanced(owner, kind, resource_id, record)
    elif kind == "models":
        application = AgentResourceApplication(owner)
        if action == "test":
            return application.test_available_model(resource_id)
        if action == "toggle":
            from kairospy.strategy.apps.agent.application import (
                AvailableModelApplication,
            )

            return AvailableModelApplication(owner).set_enabled(
                resource_id, enabled=not bool(record.get("enabled", True))
            )
        if action == "advanced":
            return _advanced(owner, kind, resource_id, record)
    elif kind == "model_endpoints":
        from kairospy.strategy.apps.agent.application import ModelEndpointApplication

        application = ModelEndpointApplication(owner)
        if action == "discover":
            return {
                "endpoint_id": resource_id,
                "models": list(application.discover_models(resource_id)),
            }
        if action == "toggle":
            return application.set_enabled(
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
    raise ValueError(f"unsupported {kind} resource action: {action}")


def preview_action(
    kind: str,
    record: Mapping[str, Any],
    action: str,
    *,
    value: str | None = None,
) -> dict[str, Any]:
    return {
        "status": "preview",
        "kind": kind,
        "resource": identity(kind, record),
        "action": action,
        "value": value,
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
        uses = MarketProviderBindingApplication(owner).references(resource_id)
    elif kind == "notifications":
        uses = references.destination_references(resource_id)
    elif kind == "models":
        uses = references.available_model_references(resource_id)
    elif kind == "model_endpoints":
        uses = references.model_endpoint_references(resource_id)
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
        "models": "available_model",
        "model_endpoints": "model_endpoint",
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
