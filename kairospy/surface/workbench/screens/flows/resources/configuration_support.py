"""Reusable interaction builders for the Resources configuration flow."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from uuid import uuid4

from rich.console import Group
from rich.panel import Panel
from rich.pretty import Pretty
from rich.table import Table
from rich.text import Text

from kairospy.strategy.apps.agent.application import (
    AgentResourceApplication,
    ModelConnectionDraftApplication,
)
from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
)

from ....widgets import (
    ActionToken,
    ActionItem,
    ChoiceInteraction,
    ControlInteraction,
    Feature,
    InputInteraction,
    InteractionHeading,
    renderable_plain_text,
)
from ...activity import ActivityKind, ActivityOutcome, ActivityRecord
from ...effects import (
    AppendActivity,
    RunOperation,
    ScreenEffect,
    SetInteraction,
    SetStatus,
)
from ...navigation.catalog import (
    AI_MODEL_ACTIONS,
    ResourceTask,
    SECTION_ACTIONS,
)
from ...session import GuidedSession
from .views import (
    RESOURCE_LABELS,
    action_result_renderable,
    mapping_renderable,
    model_catalog_renderable,
    record_summary,
    records_renderable,
    saved_resource_renderable,
)
from .wizard import ResourceWizardState, prepare_model_draft, save_resource_wizard
from .actions import (
    detail_actions,
    detail_renderable,
    execute_action,
    identity,
    list_records,
    preview_action,
    summary,
    summary_renderable,
)
from ...navigation import (
    Routes,
    Section,
    action_id,
    belongs_to,
    context_items,
    context_label,
    route,
)
from ...operation import OperationSpec
from ...results import ResultKind, ResultRoute
from ...selection import (
    ResourceRecordView,
    SelectionRecord,
    selected_value,
    selection_records,
)


def _workspace_owner(state: Any) -> Any:
    if state.owner is None:
        raise RuntimeError(state.load_error or "当前没有可用的 workspace")
    return state.owner


_NOTIFICATION_PROVIDER_ACTIONS = (
    ActionItem("feishu", "飞书（推荐）", "使用群机器人 Webhook", "1"),
    ActionItem("telegram", "Telegram", "使用机器人令牌和 Chat ID", "2"),
)

_ACCOUNT_MODE_ACTIONS = (
    ActionItem("paper", "模拟账户", "使用本地余额，不连接交易所", "1"),
    ActionItem("live", "交易所账户", "连接 Binance 或 OKX 的真实账户", "2"),
)

_ACCOUNT_PROVIDER_ACTIONS = (
    ActionItem("binance", "Binance", "连接 Binance API", "1"),
    ActionItem("okx", "OKX（原 OKEx）", "连接 OKX API", "2"),
)

_ACCOUNT_ROLE_ACTIONS = (
    ActionItem(
        "readonly",
        "只读（推荐）",
        "只读取账户、余额和持仓，不下单、不转账",
        "1",
    ),
    ActionItem("trade", "交易", "允许读取并执行订单；需要交易权限", "2"),
)

_ACCOUNT_ACCESS_ACTIONS = (
    ActionItem(
        "account-read",
        "账户只读访问",
        "读取身份、余额、持仓和账户事件，不允许下单",
        "1",
    ),
    ActionItem(
        "order-trade",
        "订单交易访问",
        "允许订单查询、提交、修改和撤销；不允许资金转移",
        "2",
    ),
)

_ACCOUNT_SEGMENT_ACTIONS = (
    ActionItem("spot", "现货", "读取现货账户余额和持仓", "1"),
    ActionItem("perpetual", "永续合约", "读取永续合约账户和持仓", "2"),
)

_DATA_PROVIDER_ACTIONS = (
    ActionItem("massive", "Massive", "美股、期权目录与行情", "1"),
    ActionItem("binance", "Binance", "现货、合约或鉴权行情", "2"),
    ActionItem("okx", "OKX（原 OKEx）", "现货、永续、期货或期权行情", "3"),
)

_CREDENTIAL_MODE_ACTIONS = (
    ActionItem("existing", "选择已有凭据", "复用同 Provider 的 Workspace 凭据", "1"),
    ActionItem("new", "安全创建新凭据", "在隐藏输入中填写认证字段", "2"),
)


def _data_product_actions(wizard: ResourceWizardState) -> tuple[ActionItem, ...]:
    provider = str(
        wizard.answers.get("data-provider")
        or wizard.record.get("provider")
        or "massive"
    )
    products = {
        "massive": (
            ("equity", "美股", "Reference 目录与美股行情"),
            ("options", "美股期权", "Reference 目录与期权行情"),
        ),
        "binance": (
            ("spot", "现货", "现货行情查询与订阅"),
            ("equity", "股票", "需要鉴权的股票行情"),
            ("usd-m-futures", "U 本位合约", "U 本位合约行情"),
            ("coin-m-futures", "币本位合约", "币本位合约行情"),
        ),
        "okx": (
            ("spot", "现货", "现货行情查询与订阅"),
            ("swap", "永续合约", "永续合约行情查询与订阅"),
            ("futures", "交割合约", "交割合约行情查询与订阅"),
            ("options", "期权", "期权行情查询与订阅"),
        ),
    }.get(provider, ())
    return tuple(
        ActionItem(product, label, description, str(index))
        for index, (product, label, description) in enumerate(products, 1)
    )


def _credential_actions(wizard: ResourceWizardState) -> tuple[ActionItem, ...]:
    provider = str(
        wizard.answers.get("data-provider")
        or wizard.answers.get("account-provider")
        or wizard.record.get("provider")
        or wizard.record.get("broker")
        or ""
    )
    values = [
        value for value in wizard.credentials if value.get("provider") == provider
    ]
    return tuple(
        ActionItem(
            str(value["credential_id"]),
            str(value["credential_id"]),
            "已配置 · Secret 不会显示",
            str(index),
        )
        for index, value in enumerate(values, 1)
    )


def _account_credential_actions(
    state: Any, record: Mapping[str, object]
) -> tuple[ActionItem, ...]:
    provider = str(record.get("integration_provider") or record.get("broker") or "")
    if state.owner is None:
        return ()
    try:
        credentials = CredentialConfigurationApplication(state.owner).list()
    except (AttributeError, OSError, ValueError):
        return ()
    return tuple(
        ActionItem(
            str(value["credential_id"]),
            str(value["credential_id"]),
            f"{provider} · Secret 不会显示",
            str(index),
        )
        for index, value in enumerate(
            (item for item in credentials if item.get("provider") == provider), 1
        )
    )


def _account_access_summary(
    record: Mapping[str, object], purpose: str, credential_id: str | None = None
) -> Table:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("账户", identity("accounts", record))
    table.add_row(
        "Provider",
        str(record.get("integration_provider") or record.get("broker") or "—"),
    )
    table.add_row("环境", str(record.get("environment") or "—"))
    segments = record.get("segments")
    table.add_row(
        "产品",
        "/".join(map(str, segments)) if isinstance(segments, list) else "—",
    )
    if purpose:
        table.add_row("Kairos 用途", purpose)
    if credential_id:
        table.add_row("凭据", credential_id)
    permissions = record.get("permissions")
    if isinstance(permissions, Mapping) and permissions:
        table.add_row(
            "Provider 实际权限",
            ", ".join(
                str(name)
                for name, state in permissions.items()
                if str(state).lower() in {"granted", "true", "enabled"}
            )
            or "尚未发现",
        )
    if purpose == "order-trade":
        table.add_row(
            "后果",
            Text(
                "验证通过后可提交、修改和撤销订单；仍不授权资金转移。", style="yellow"
            ),
        )
    elif purpose == "account-read":
        table.add_row("后果", "只读取账户事实，不授权订单命令")
    return table


def _model_provider_actions(
    wizard: ResourceWizardState,
) -> tuple[ActionItem, ...]:
    descriptions = {
        "openai": "使用 Responses API",
        "anthropic": "使用 Claude Messages API",
        "openrouter": "通过统一接口使用多个模型服务",
        "ollama": "连接 Ollama，默认无需 API Key",
        "lmstudio": "连接 LM Studio，默认无需 API Key",
    }
    values = wizard.model_providers or tuple(
        {"provider": provider, "label": label}
        for provider, label in (
            ("openai", "OpenAI"),
            ("anthropic", "Anthropic"),
            ("openrouter", "OpenRouter"),
            ("ollama", "Ollama"),
            ("lmstudio", "LM Studio"),
        )
    )
    actions = tuple(
        ActionItem(
            str(value["provider"]),
            str(value.get("label") or value["provider"])
            + (
                "（推荐）"
                if value["provider"] == "openai"
                else "（本地）"
                if value.get("group") == "local"
                else ""
            ),
            descriptions.get(str(value["provider"]), "连接模型服务"),
            str(index),
        )
        for index, value in enumerate(values, 1)
    )
    return (
        *actions,
        ActionItem(
            "custom",
            "自定义服务",
            "连接兼容接口或企业网关",
            str(len(actions) + 1),
        ),
    )


def _model_endpoint_actions(
    wizard: ResourceWizardState,
) -> tuple[ActionItem, ...]:
    existing = tuple(
        ActionItem(
            str(value["endpoint_id"]),
            str(value["endpoint_id"]),
            str(value.get("provider_label") or value.get("provider") or "模型服务")
            + " · 已配置",
            str(index),
        )
        for index, value in enumerate(wizard.model_endpoints, 1)
    )
    has_existing = bool(existing)
    return (
        *existing,
        ActionItem(
            "__new_endpoint__",
            "添加另一个模型服务" if has_existing else "配置模型服务",
            (
                "选择服务商，并填写 API Key 或本地服务地址"
                if not has_existing
                else "配置完成后返回当前模型"
            ),
            str(len(existing) + 1),
        ),
    )


_MODEL_MODE_ACTIONS = (
    ActionItem("openai-responses", "OpenAI Responses", "兼容 Responses API", "1"),
    ActionItem(
        "openai-chat-completions",
        "OpenAI Chat Completions",
        "兼容 Chat Completions API",
        "2",
    ),
    ActionItem("anthropic-messages", "Anthropic Messages", "兼容 Messages API", "3"),
    ActionItem("ollama-native", "Ollama Native", "使用 Ollama 原生接口", "4"),
)
