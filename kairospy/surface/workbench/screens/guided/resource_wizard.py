"""Staged runtime-resource configuration wizard and persistence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from kairospy.investment.apps.account.application import AccountConfigurationApplication
from kairospy.investment.apps.reference.application import (
    ReferenceProviderConfigurationApplication,
)
from kairospy.strategy.apps.agent.application import AgentResourceApplication
from kairospy.strategy.apps.notification.application import NotificationAdminApplication
from kairospy.system.apps.credentials.application import (
    CredentialConfigurationApplication,
    SecretRef,
)

from .resource_rendering import identity


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
                detail = (
                    "修改时直接回车沿用现有凭据；内容不会显示、记录或进入命令历史。"
                    if self.editing
                    else "内容不会显示、记录或进入命令历史。"
                )
            else:
                detail = (
                    f"直接回车使用 {default}；输入 /back 取消整个向导。"
                    if default
                    else "可直接回车留空；输入 /back 取消整个向导。"
                )
            return name, _RESOURCE_PROMPTS[name], detail, secret
        return None

    def accept(self, name: str, raw: str) -> None:
        value = raw.strip() or self._default(name)
        if name in {"include-options"}:
            self.answers[name] = _parse_bool(value)
            return
        allowed = {
            "account-mode": {"paper", "live"},
            "account-segment": {"spot", "perpetual"},
            "account-provider": {"binance", "okx"},
            "account-role": {"readonly", "trade"},
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
            raise ValueError(
                f"{_RESOURCE_PROMPTS[name]}必须是：" + "、".join(sorted(allowed[name]))
            )
        if name == "models":
            self.answers[name] = tuple(
                item.strip() for item in value.split(",") if item.strip()
            )
            return
        if name == "resource-id" and not value:
            raise ValueError("资源名称不能为空")
        self.answers[name] = value

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
        for key in tuple(self.answers):
            if key.startswith("secret-"):
                self.answers[key] = ""

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
            steps = ["account-mode", *identity, "account-segment"]
            if mode == "paper":
                if not self.editing:
                    steps.append("account-balance")
            else:
                steps.extend(
                    (
                        "account-provider",
                        "account-role",
                        "secret-primary",
                        "secret-secondary",
                    )
                )
                if provider == "okx":
                    steps.append("secret-tertiary")
            return tuple(steps)
        if self.kind == "data":
            return (*identity, "endpoint", "include-options", "secret-primary")
        if self.kind == "models":
            return (
                "model-provider",
                *identity,
                "model-mode",
                "endpoint",
                "models",
                "secret-primary",
            )
        provider = str(
            self.answers.get("notification-provider")
            or self._default("notification-provider")
        )
        steps = ["notification-provider", *identity, "secret-primary"]
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
        model_mode, model_endpoint = _MODEL_DEFAULTS.get(
            model_provider, _MODEL_DEFAULTS["custom"]
        )
        defaults: dict[str, object] = {
            "account-mode": self.record.get("environment") or "paper",
            "resource-id": identity(self.kind, self.record)
            if self.record
            else {
                "accounts": "paper-main",
                "data": "massive-readonly",
                "models": f"{provider}-main",
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
            "endpoint": (
                self.record.get("endpoint") or "https://api.massive.com"
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
            "models": ",".join(str(item) for item in self.record.get("models") or ()),
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
        identity(wizard.kind, record) if record else str(answers["resource-id"])
    )
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
        role = str(answers["account-role"])
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
            CredentialConfigurationApplication(owner).configure_secret_values(
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
        secret = str(answers.get("secret-primary") or "")
        credential_id = str(record.get("credential_id") or resource_id)
        if secret:
            CredentialConfigurationApplication(owner).configure_secret_values(
                credential_id,
                provider="massive",
                values={"api_key": secret},
                overwrite=bool(record),
            )
        elif not record:
            raise ValueError("Massive 连接需要 API Key")
        capabilities = ["reference", "equity_market"]
        if answers["include-options"]:
            capabilities.append("options")
        return ReferenceProviderConfigurationApplication(owner).configure_massive(
            credential_id=credential_id,
            endpoint=str(answers["endpoint"]),
            capabilities=capabilities,
        )
    if wizard.kind == "models":
        provider = str(answers["model-provider"])
        secret = str(answers.get("secret-primary") or "")
        credential_id: str | None = None
        if secret:
            credential_id = str(record.get("credential_id") or f"{resource_id}-auth")
            credential_provider = (
                provider
                if provider in {"openai", "anthropic", "openrouter"}
                else "custom-model"
            )
            CredentialConfigurationApplication(owner).configure_secret_values(
                credential_id,
                provider=credential_provider,
                values={"api_key": secret},
                role="model-inference",
                overwrite=bool(record),
            )
        elif record.get("credential_id"):
            credential_id = str(record["credential_id"])
        return AgentResourceApplication(owner).configure_model_connection(
            resource_id,
            provider=provider,
            api_mode=str(answers["model-mode"]),
            base_url=str(answers["endpoint"]),
            credential_id=credential_id,
            models=tuple(answers["models"]),
            overwrite=bool(record),
        )
    provider = str(answers["notification-provider"])
    secret = str(answers.get("secret-primary") or "")
    credential_id = str(record.get("credential_id") or resource_id)
    field_name = "bot_token" if provider == "telegram" else "webhook_url"
    credentials = CredentialConfigurationApplication(owner)
    if secret:
        credential = credentials.configure_secret_values(
            credential_id,
            provider=provider,
            values={field_name: secret},
            role="notification-send",
            overwrite=bool(record),
        )
    elif record:
        credential = credentials.show(credential_id)
    else:
        raise ValueError("通知提醒需要 Webhook URL 或 Bot Token")
    raw_refs = credential["secret_refs"]
    if not isinstance(raw_refs, Mapping) or not isinstance(
        raw_refs.get(field_name), Mapping
    ):
        raise ValueError("通知凭据缺少 SecretRef")
    reference = raw_refs[field_name]
    return NotificationAdminApplication(owner).configure(
        resource_id,
        provider=provider,  # type: ignore[arg-type]
        credential_id=credential_id,
        secret_ref=SecretRef(str(reference["source"]), str(reference["id"])),  # type: ignore[arg-type]
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
    "endpoint": "API endpoint",
    "include-options": "是否包含期权目录和行情（yes / no）",
    "model-provider": "模型服务",
    "model-mode": "接口模式",
    "models": "已知模型（逗号分隔，可留空）",
    "notification-provider": "通知渠道（feishu / telegram）",
    "chat-id": "Telegram chat_id",
    "secret-primary": "安全凭据",
    "secret-secondary": "API Secret",
    "secret-tertiary": "Passphrase",
}


def _owner(state: Any) -> Any:
    if state.owner is None:
        raise RuntimeError(state.load_error or "当前没有可用的 workspace")
    return state.owner


__all__ = ["ResourceWizardState", "save_resource_wizard"]
