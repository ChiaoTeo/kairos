"""Configuration wizard owned by the Resources Workbench slice."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import re
from typing import Any

from kairospy.investment.apps.account.application import AccountConfigurationApplication
from kairospy.investment.apps.market.application import MarketProviderBindingApplication
from kairospy.investment.apps.reference.application import (
    ReferenceProviderConfigurationApplication,
)
from kairospy.strategy.apps.agent.application import (
    AvailableModelApplication,
    ModelConnectionDraft,
    ModelConnectionDraftApplication,
    ModelEndpointApplication,
)
from kairospy.strategy.apps.notification.application import NotificationAdminApplication
from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
)
from kairospy.system.apps.integration.application import (
    ProviderConnectionConfigurationApplication,
)
from kairospy.system.apps.workspace.application import WorkspaceConfigurationTransaction

from .views import identity


_MODEL_DEFAULTS: dict[str, tuple[str, str]] = {
    "openai": ("openai-responses", "https://api.openai.com/v1"),
    "anthropic": ("anthropic-messages", "https://api.anthropic.com/v1"),
    "openrouter": ("openai-chat-completions", "https://openrouter.ai/api/v1"),
    "ollama": ("openai-chat-completions", "http://127.0.0.1:11434/v1"),
    "lmstudio": ("openai-chat-completions", "http://127.0.0.1:1234/v1"),
    "custom": ("openai-chat-completions", ""),
}


@dataclass(slots=True)
class ResourceWizardState:
    """One-resource staged values; secrets live only until save/cancel."""

    kind: str
    record: dict[str, Any] = field(default_factory=dict)
    answers: dict[str, Any] = field(default_factory=dict)
    generated_id: str | None = None
    model_draft: ModelConnectionDraft | None = field(default=None, repr=False)
    discovered_models: tuple[dict[str, object], ...] = ()
    selected_model: str | None = None
    model_phase: str = "fields"
    endpoint_defaulted: bool = False
    model_providers: tuple[dict[str, object], ...] = ()
    credentials: tuple[dict[str, object], ...] = ()
    model_endpoints: tuple[dict[str, object], ...] = ()

    @property
    def editing(self) -> bool:
        return bool(self.record)

    def next_prompt(self) -> tuple[str, str, str, bool] | None:
        for name in self._steps():
            if name in self.answers:
                continue
            default = self._default(name)
            secret = name.startswith("secret-")
            if secret:
                if self.kind == "notifications":
                    provider = str(
                        self.answers.get("notification-provider")
                        or self._default("notification-provider")
                    )
                    detail = (
                        "从 BotFather 获取；内容会安全保存，不会显示或进入命令历史。"
                        if provider == "telegram"
                        else "在飞书群设置中添加自定义机器人后复制；内容会安全保存，不会显示或进入命令历史。"
                    )
                    if self.editing:
                        detail = f"直接回车沿用现有凭据；{detail}"
                elif self.kind == "model_endpoints":
                    if self.editing and not self.model_provider_changed():
                        detail = (
                            "直接回车沿用现有 API Key；如需更换，请粘贴新值。"
                            "内容不会显示、记录或进入命令历史。"
                        )
                    else:
                        detail = (
                            f"请输入 {self.model_provider_label()} API Key；"
                            "内容不会显示、记录或进入命令历史。"
                        )
                else:
                    detail = (
                        "修改时直接回车沿用现有凭据；内容不会显示、记录或进入命令历史。"
                        if self.editing
                        else "内容不会显示、记录或进入命令历史。"
                    )
            elif name == "notification-provider":
                detail = "通过选项选择通知渠道；输入 /back 返回，/cancel 取消向导。"
            elif name == "chat-id":
                detail = (
                    "请输入接收通知的 Chat ID，例如 123456789 或 -1001234567890；"
                    "输入 /back 返回，/cancel 取消向导。"
                )
            elif name == "provider-model":
                detail = (
                    "输入服务商接受的精确模型 ID，例如 gpt-5.6-sol 或 qwen3:8b；"
                    "输入 /back 返回。"
                )
            elif name == "endpoint" and self.kind == "model_endpoints":
                provider_default = self.model_provider_default("base_url")
                current = str(self.record.get("base_url") or "")
                if provider_default:
                    detail = (
                        f"当前地址为 {current}；直接回车恢复服务默认地址 {provider_default}。"
                        if self.editing and current != provider_default
                        else f"直接回车使用服务默认地址 {provider_default}。"
                    ) + "使用代理、企业网关或远程实例时请输入完整地址。"
                else:
                    detail = "请输入完整的 HTTP(S) API 地址，例如 https://ai.example.com/v1。"
                detail += "输入 /back 返回，/cancel 取消向导。"
            else:
                detail = (
                    f"直接回车使用 {default}；输入 /back 返回，/cancel 取消向导。"
                    if default
                    else "可直接回车留空；输入 /back 返回，/cancel 取消向导。"
                )
            return name, self.prompt_label(name), detail, secret
        return None

    def accept(self, name: str, raw: str) -> None:
        entered = raw.strip()
        value = entered or self._default(name)
        if name == "endpoint" and self.kind == "model_endpoints" and not entered:
            value = str(self.model_provider_default("base_url"))
        if name == "account-mode":
            value = {"1": "paper", "2": "live"}.get(value.lower(), value.lower())
        if name == "account-provider":
            value = {"1": "binance", "2": "okx", "okex": "okx"}.get(
                value.lower(), value.lower()
            )
        if name == "account-role":
            value = {"1": "readonly", "2": "trade"}.get(value.lower(), value.lower())
        if name == "account-segment":
            value = {"1": "spot", "2": "perpetual"}.get(value.lower(), value.lower())
        if name == "data-provider":
            value = {"1": "massive", "2": "binance", "3": "okx"}.get(
                value.lower(), value.lower()
            )
        if name == "credential-mode":
            value = {"1": "existing", "2": "new"}.get(value.lower(), value.lower())
        if name == "notification-provider":
            value = {"1": "feishu", "2": "telegram"}.get(value.lower(), value.lower())
        if name in {"include-options"}:
            self.answers[name] = _parse_bool(value)
            return
        allowed = {
            "account-mode": {"paper", "live"},
            "account-segment": {"spot", "perpetual"},
            "account-provider": {"binance", "okx"},
            "account-role": {"readonly", "trade"},
            "data-provider": {"massive", "binance", "okx"},
            "credential-mode": {"existing", "new"},
            "model-provider": {
                "openai",
                "anthropic",
                "openrouter",
                "ollama",
                "lmstudio",
                "custom",
            },
            "model-mode": {
                "openai-responses",
                "openai-chat-completions",
                "anthropic-messages",
                "ollama-native",
            },
            "notification-provider": {"feishu", "telegram"},
        }
        if name in allowed and value not in allowed[name]:
            if name == "notification-provider":
                raise ValueError("请输入 1（飞书）或 2（Telegram）")
            raise ValueError(
                f"{_RESOURCE_PROMPTS[name]}必须是：" + "、".join(sorted(allowed[name]))
            )
        if name == "data-product":
            provider = str(
                self.answers.get("data-provider") or self._default("data-provider")
            )
            if value not in _data_products(provider):
                raise ValueError(
                    "行情产品必须是：" + "、".join(_data_products(provider))
                )
        if name == "credential-mode" and value == "existing":
            provider = str(
                self.answers.get("data-provider")
                or self.answers.get("account-provider")
                or self.record.get("provider")
                or self.record.get("broker")
                or ""
            )
            if not any(item.get("provider") == provider for item in self.credentials):
                raise ValueError(f"尚无可用的 {provider} 凭据，请选择安全创建新凭据")
        if name == "endpoint" and self.kind == "model_endpoints" and not value:
            raise ValueError("自定义模型服务需要 API 地址")
        if name == "endpoint" and self.kind == "model_endpoints":
            self.endpoint_defaulted = not entered
        if (
            name == "secret-primary"
            and self.kind == "model_endpoints"
            and self.model_auth_required()
            and not value
            and (not self.editing or self.model_provider_changed())
        ):
            raise ValueError(f"{self.model_provider_label()} 需要 API Key")
        if name == "provider-model" and (
            not value or any(character.isspace() for character in value)
        ):
            raise ValueError("服务商模型 ID 不能为空或包含空白字符")
        if (
            name == "resource-id"
            and self.kind == "models"
            and not re.fullmatch(
                r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,126}[A-Za-z0-9_-])?", value
            )
        ):
            raise ValueError(
                "模型名称必须以英文字母或数字开头，且只能包含英文字母、"
                "数字、内部点号、连字符或下划线（最长 128 个字符）"
            )
        if name == "resource-id" and not value:
            raise ValueError("资源名称不能为空")
        self.answers[name] = value

    def prompt_label(self, name: str) -> str:
        if name == "notification-provider":
            return "请选择通知渠道（输入 1 或 2）"
        if name == "secret-primary" and self.kind == "notifications":
            provider = str(
                self.answers.get("notification-provider")
                or self._default("notification-provider")
            )
            return (
                "Telegram Bot Token"
                if provider == "telegram"
                else "飞书机器人 Webhook 地址"
            )
        if name == "secret-primary" and self.kind == "model_endpoints":
            return f"{self.model_provider_label()} API Key"
        return _RESOURCE_PROMPTS[name]

    def model_provider(self) -> str:
        return str(
            self.answers.get("model-provider")
            or self.record.get("provider")
            or "openai"
        )

    def model_provider_label(self) -> str:
        configured = next(
            (
                value.get("label")
                for value in self.model_providers
                if value.get("provider") == self.model_provider()
            ),
            None,
        )
        if configured:
            return str(configured)
        return {
            "openai": "OpenAI",
            "anthropic": "Anthropic",
            "openrouter": "OpenRouter",
            "ollama": "Ollama",
            "lmstudio": "LM Studio",
            "custom": "自定义服务",
        }.get(self.model_provider(), self.model_provider())

    def model_provider_changed(self) -> bool:
        selected = self.answers.get("model-provider")
        return bool(
            selected
            and self.record
            and str(selected) != str(self.record.get("provider") or "")
        )

    def model_auth_required(self) -> bool:
        value = self.model_provider_default("auth_required")
        return bool(value) if value != "" else True

    def model_provider_default(self, name: str) -> object:
        provider = self.model_provider()
        configured = next(
            (
                value
                for value in self.model_providers
                if value.get("provider") == provider
            ),
            None,
        )
        if configured is not None:
            return configured.get(name, "")
        mode, endpoint = _MODEL_DEFAULTS.get(provider, _MODEL_DEFAULTS["custom"])
        return {
            "api_mode": mode,
            "base_url": endpoint,
            "auth_required": provider not in {"ollama", "lmstudio"},
        }.get(name, "")

    def step_progress(self) -> tuple[int, int]:
        steps = self._steps()
        completed = sum(name in self.answers for name in steps)
        confirmation = 1 if self.kind == "notifications" else 0
        return (
            min(completed + 1, len(steps) + confirmation),
            len(steps) + confirmation,
        )

    def go_back(self) -> bool:
        """Forget the latest staged answer so the preceding step can be edited."""

        completed = [name for name in self._steps() if name in self.answers]
        if not completed:
            return False
        name = completed[-1]
        self.answers.pop(name, None)
        if name == "notification-provider":
            self.generated_id = None
        return True

    def redacted_summary(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "editing": self.editing,
            **{
                key: ("<configured>" if value else "<unchanged-or-empty>")
                if key.startswith("secret-")
                else value
                for key, value in self.answers.items()
            },
        }

    def clear_secrets(self) -> None:
        self.discard_model_draft()
        for key in tuple(self.answers):
            if key.startswith("secret-"):
                self.answers[key] = ""

    def discard_model_draft(self) -> None:
        if self.model_draft is not None:
            self.model_draft.discard()
            self.model_draft = None

    def _steps(self) -> tuple[str, ...]:
        identity = () if self.editing else ("resource-id",)
        if self.kind == "accounts":
            mode = str(
                self.answers.get("account-mode") or self._default("account-mode")
            )
            provider = str(
                self.answers.get("account-provider")
                or self._default("account-provider")
            )
            steps = ["account-mode"]
            if mode == "paper":
                steps.extend((*identity, "account-segment"))
                if not self.editing:
                    steps.append("account-balance")
            else:
                steps.extend(
                    (
                        "account-provider",
                        *identity,
                        "account-segment",
                        "secret-primary",
                        "secret-secondary",
                    )
                )
                if provider == "okx":
                    steps.append("secret-tertiary")
            return tuple(steps)
        if self.kind == "data":
            provider = str(
                self.answers.get("data-provider") or self._default("data-provider")
            )
            credential_mode = str(
                self.answers.get("credential-mode") or self._default("credential-mode")
            )
            steps = [
                "data-provider",
                *identity,
                "data-product",
                "endpoint",
                "credential-mode",
            ]
            if credential_mode == "existing":
                steps.append("credential-id")
            else:
                steps.append("secret-primary")
                if provider != "massive":
                    steps.append("secret-secondary")
                if provider == "okx":
                    steps.append("secret-tertiary")
            return tuple(steps)
        if self.kind == "model_endpoints":
            provider = self.model_provider()
            steps = ["model-provider", *identity]
            if provider == "custom":
                steps.append("model-mode")
            steps.append("endpoint")
            if self.model_auth_required():
                steps.append("secret-primary")
            return tuple(steps)
        if self.kind == "models":
            return ("endpoint-id", *identity, "provider-model")
        provider = str(
            self.answers.get("notification-provider")
            or self._default("notification-provider")
        )
        steps = ["notification-provider", "secret-primary"]
        if provider == "telegram":
            steps.append("chat-id")
        return tuple(steps)

    def _default(self, name: str) -> str:
        provider = str(
            self.answers.get("model-provider")
            or self.answers.get("notification-provider")
            or self.record.get("provider")
            or "openai"
        )
        model_provider = str(
            self.answers.get("model-provider")
            or self.record.get("provider")
            or "openai"
        )
        provider_changed = bool(
            self.answers.get("model-provider")
            and model_provider != str(self.record.get("provider") or model_provider)
        )
        model_mode = str(self.model_provider_default("api_mode"))
        model_endpoint = str(self.model_provider_default("base_url"))
        data_provider = str(
            self.answers.get("data-provider")
            or self.record.get("provider")
            or "massive"
        )
        data_product = str(
            self.answers.get("data-product")
            or next(
                (
                    item
                    for item in self.record.get("products") or ()
                    if item != "reference"
                ),
                "equity" if data_provider == "massive" else "spot",
            )
        )
        defaults: dict[str, object] = {
            "account-mode": self.record.get("environment") or "paper",
            "resource-id": identity(self.kind, self.record)
            if self.record
            else {
                "accounts": (
                    f"{self.answers.get('account-provider') or 'binance'}-"
                    f"{self.answers.get('account-role') or 'readonly'}"
                    if self.answers.get("account-mode") == "live"
                    else "paper-main"
                ),
                "data": f"{data_provider}-{data_product}",
                "models": f"{provider}-main",
                "model_endpoints": f"{provider}-main",
                "notifications": f"{provider}-alerts",
            }[self.kind],
            "account-segment": next(
                iter(
                    self.record.get("segments")
                    or (self.record.get("segment") or "spot",)
                ),
                "spot",
            ),
            "account-balance": "USDT=100000",
            "account-provider": self.record.get("broker") or "binance",
            "account-role": self.record.get("credential_role") or "readonly",
            "data-provider": data_provider,
            "data-product": data_product,
            "credential-mode": "existing" if self.editing else "new",
            "credential-id": self.record.get("credential_id") or "",
            "endpoint": (
                self.record.get("endpoint")
                or _data_endpoint(data_provider, data_product)
                if self.kind == "data"
                else (
                    self.record.get("base_url") or model_endpoint
                    if self.editing and not provider_changed
                    else model_endpoint
                )
            ),
            "include-options": _bool_text(
                "options" in (self.record.get("capabilities") or ())
            ),
            "model-provider": self.record.get("provider") or "openai",
            "model-mode": (
                self.record.get("api_mode") or model_mode
                if self.editing and not provider_changed
                else model_mode
            ),
            "endpoint-id": self.record.get("endpoint_id")
            or (
                str(self.model_endpoints[0]["endpoint_id"])
                if self.model_endpoints
                else ""
            ),
            "provider-model": self.record.get("provider_model") or "",
            "notification-provider": self.record.get("provider") or "feishu",
            "chat-id": self.record.get("chat_id") or "",
            "secret-primary": "",
            "secret-secondary": "",
            "secret-tertiary": "",
        }
        return str(defaults[name])


def save_resource_wizard(state: Any, wizard: ResourceWizardState) -> dict[str, Any]:
    owner = _owner(state)
    record = wizard.record
    answers = wizard.answers
    resource_id = (
        identity(wizard.kind, record)
        if record
        else str(answers.get("resource-id") or wizard.generated_id or "")
    )
    if not resource_id:
        raise ValueError("资源名称尚未生成")
    if wizard.kind == "accounts":
        application = AccountConfigurationApplication(owner)
        mode = str(answers["account-mode"])
        segment = str(answers["account-segment"])
        if mode == "paper":
            if record:
                return application.modify(
                    resource_id, segment=segment, environment="paper"
                )
            balance = str(answers.get("account-balance") or "")
            return application.simulate(
                resource_id,
                segment=segment,
                initial_balances=(balance,) if balance else (),
            )
        provider = str(answers["account-provider"])
        role = str(answers.get("account-role") or "readonly")
        credential_id = str(record.get("credential_id") or f"{resource_id}-credential")
        values = {
            "api_key": str(answers.get("secret-primary") or ""),
            "api_secret": str(answers.get("secret-secondary") or ""),
        }
        if provider == "okx":
            values["passphrase"] = str(answers.get("secret-tertiary") or "")
        if any(values.values()) and not all(values.values()):
            raise ValueError("更新实盘凭据时必须填写完整的认证字段")
        if all(values.values()):
            CredentialConfigurationApplication(owner).configure(
                credential_id,
                provider=provider,
                values=values,
                role=role,
                overwrite=bool(record),
            )
        elif not record:
            raise ValueError("实盘账户需要完整安全凭据")
        return application.connect(
            resource_id,
            broker=provider,
            integration_provider=provider,
            segment=segment,
            environment="live",
            credential=credential_id,
            credential_role=role,
            force=bool(record),
        )
    if wizard.kind == "data":
        provider = str(
            answers.get("data-provider") or record.get("provider") or "massive"
        )
        product = str(
            answers.get("data-product")
            or next(
                (
                    value
                    for value in record.get("products") or ()
                    if value != "reference"
                ),
                "options"
                if "options" in (record.get("capabilities") or ())
                else "equity"
                if provider == "massive"
                else "spot",
            )
        )
        credential_mode = str(
            answers.get("credential-mode")
            or ("new" if answers.get("secret-primary") else "existing")
        )
        credentials = CredentialConfigurationApplication(owner)
        if credential_mode == "existing":
            credential_id = str(answers["credential-id"])
            credential = credentials.show(credential_id)
            if credential.get("provider") != provider:
                raise ValueError(f"请选择 {provider} 的凭据")
        else:
            credential_id = str(
                record.get("credential_id") or f"{resource_id}-credential"
            )
            values = {"api_key": str(answers.get("secret-primary") or "")}
            if provider != "massive":
                values["api_secret"] = str(answers.get("secret-secondary") or "")
            if provider == "okx":
                values["passphrase"] = str(answers.get("secret-tertiary") or "")
            credentials.configure(
                credential_id,
                provider=provider,
                values=values,
                role="readonly",
                overwrite=bool(record and record.get("credential_id") == credential_id),
            )
        if provider == "massive":
            capabilities = [
                "reference",
                "options" if product == "options" else "equity_market",
            ]
            connection = ReferenceProviderConfigurationApplication(
                owner
            ).configure_massive(
                credential_id=credential_id,
                endpoint=str(answers["endpoint"]),
                capabilities=capabilities,
            )
            MarketProviderBindingApplication(owner).bind_connection(
                "massive", product=product
            )
            return connection
        connection = ProviderConnectionConfigurationApplication(owner).configure(
            resource_id,
            provider=provider,
            credential_id=credential_id,
            products=(product,),
            purposes=("market-query", "market-stream"),
            endpoint=str(answers["endpoint"]),
            overwrite=bool(record),
        )
        MarketProviderBindingApplication(owner).bind_connection(
            resource_id, product=product
        )
        return connection
    if wizard.kind == "model_endpoints":
        provider = str(answers["model-provider"])
        endpoint_id = resource_id
        auth_required = wizard.model_auth_required()
        credential_id = str(record.get("credential_id") or f"{endpoint_id}-auth")
        secret = str(answers.get("secret-primary") or "")
        credentials = CredentialConfigurationApplication(owner)
        prepared_credential = None
        if auth_required and secret:
            prepared_credential = credentials.prepare(
                credential_id,
                provider=(
                    provider
                    if provider in {"openai", "anthropic", "openrouter"}
                    else "custom-model"
                ),
                role="model-inference",
                values={"api_key": secret},
            )
        elif auth_required and not record:
            raise ValueError("模型服务需要 API Key")
        prepared_endpoint = ModelEndpointApplication(owner).prepare(
            endpoint_id,
            provider=provider,
            api_mode=str(
                answers.get("model-mode") or wizard.model_provider_default("api_mode")
            ),
            base_url=str(answers["endpoint"]),
            credential_id=credential_id if auth_required else None,
            credential_provider=(
                prepared_credential.provider if prepared_credential else None
            ),
        )
        transaction = WorkspaceConfigurationTransaction(
            owner, f"model-endpoint:{endpoint_id}"
        )
        if prepared_credential is not None:
            prepared_credential.stage(transaction)
        prepared_endpoint.stage(transaction)
        transaction.commit()
        return ModelEndpointApplication(owner).show(endpoint_id)
    if wizard.kind == "models":
        return AvailableModelApplication(owner).configure(
            resource_id,
            endpoint_id=str(answers["endpoint-id"]),
            provider_model=str(answers["provider-model"]),
            overwrite=bool(record),
        )
    provider = str(answers["notification-provider"])
    secret = str(answers.get("secret-primary") or "")
    credential_id = str(record.get("credential_id") or resource_id)
    field_name = "bot_token" if provider == "telegram" else "webhook_url"
    if secret:
        pass
    elif record:
        existing = CredentialConfigurationApplication(owner).resolve_field(
            credential_id, field_name
        )
        if not existing:
            raise ValueError("通知凭据缺少认证值")
        secret = existing
    else:
        raise ValueError("通知提醒需要 Webhook URL 或 Bot Token")
    return NotificationAdminApplication(owner).configure(
        resource_id,
        provider=provider,  # type: ignore[arg-type]
        credential_id=credential_id,
        secret=secret,
        chat_id=str(answers.get("chat-id") or "") or None,
    )


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on", "是", "启用"}:
        return True
    if normalized in {"0", "false", "no", "n", "off", "否", "停用"}:
        return False
    raise ValueError("请输入 yes/no、true/false 或 1/0")


def _bool_text(value: object) -> str:
    return "yes" if bool(value) else "no"


_RESOURCE_PROMPTS = {
    "resource-id": "资源名称",
    "account-mode": "账户类型（paper / live）",
    "account-segment": "交易产品（spot / perpetual）",
    "account-balance": "初始余额（例如 USDT=100000）",
    "account-provider": "交易服务商（binance / okx）",
    "account-role": "账户用途（readonly / trade）",
    "data-provider": "行情服务商",
    "data-product": "行情产品",
    "credential-mode": "凭据方式",
    "credential-id": "已有凭据",
    "endpoint": "API 地址",
    "include-options": "是否包含期权目录和行情（yes / no）",
    "model-provider": "模型服务",
    "model-mode": "接口模式",
    "endpoint-id": "模型服务",
    "provider-model": "服务商模型 ID",
    "notification-provider": "通知渠道",
    "chat-id": "Telegram chat_id",
    "secret-primary": "安全凭据",
    "secret-secondary": "API Secret",
    "secret-tertiary": "Passphrase",
}


def _data_endpoint(provider: str, product: str) -> str:
    if provider == "massive":
        return "https://api.massive.com"
    if provider == "okx":
        return "https://www.okx.com"
    return {
        "usd-m-futures": "https://fapi.binance.com",
        "coin-m-futures": "https://dapi.binance.com",
    }.get(product, "https://api.binance.com")


def _data_products(provider: str) -> tuple[str, ...]:
    return {
        "massive": ("equity", "options"),
        "binance": ("spot", "equity", "usd-m-futures", "coin-m-futures"),
        "okx": ("spot", "swap", "futures", "options"),
    }.get(provider, ())


def _owner(state: Any) -> Any:
    if state.owner is None:
        raise RuntimeError(state.load_error or "当前没有可用的 workspace")
    return state.owner


def prepare_model_draft(
    state: Any,
    wizard: ResourceWizardState,
    *,
    models: tuple[str, ...] = (),
) -> ModelConnectionDraft:
    """Prepare one secret-safe model connection draft without persisting it."""

    owner = _owner(state)
    record = wizard.record
    connection_id = (
        identity(wizard.kind, record)
        if record
        else str(wizard.answers.get("resource-id") or "")
    )
    provider = wizard.model_provider()
    credential_id: str | None = None
    credential_values: Mapping[str, str] | None = None
    if wizard.model_auth_required():
        existing_credential = (
            None if wizard.model_provider_changed() else record.get("credential_id")
        )
        credential_id = str(existing_credential or f"{connection_id}-{provider}-auth")
        secret = str(wizard.answers.get("secret-primary") or "")
        if secret:
            credential_values = {"api_key": secret}
    return ModelConnectionDraftApplication(owner).prepare(
        connection_id,
        provider=provider,
        api_mode=str(wizard.answers.get("model-mode") or wizard._default("model-mode")),
        base_url=str(wizard.answers.get("endpoint") or wizard._default("endpoint")),
        credential_id=credential_id,
        credential_values=credential_values,
        models=models,
    )


__all__ = ["ResourceWizardState", "prepare_model_draft", "save_resource_wizard"]
