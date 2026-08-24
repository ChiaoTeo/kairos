"""Secret-safe Textual forms for workspace runtime resources."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Footer, Input, Label, Select
from textual.worker import Worker

from kairospy.application.account import AccountConfigurationApplication
from kairospy.application.agent import AgentResourceApplication
from kairospy.application.workspace.credentials import (
    CredentialConfigurationApplication,
    SecretRef,
)
from kairospy.application.notification import NotificationAdminApplication
from kairospy.application.reference import ReferenceProviderConfigurationApplication

from ..widgets import WorkspaceHeader


class ResourceSetupScreen(Screen[dict[str, Any] | None]):
    """Configure one resource without exposing credentials in argv or output."""

    TITLE = "Kairos"
    BINDINGS = [Binding("escape", "cancel", "取消")]

    def __init__(self, kind: str, record: dict[str, Any] | None = None) -> None:
        super().__init__()
        self.kind = kind
        self.record = record or {}
        self.sub_title = (
            f"首页 › 运行资源 › {_label(kind)} › {'修改' if record else '添加'}"
        )

    def compose(self) -> ComposeResult:
        yield WorkspaceHeader()
        yield Label(
            f"{'修改' if self.record else '添加'}{_label(self.kind)}", id="page-title"
        )
        with VerticalScroll(id="resource-form"):
            if self.kind == "accounts":
                yield from self._account_fields()
            elif self.kind == "data":
                yield from self._data_fields()
            elif self.kind == "models":
                yield from self._model_fields()
            else:
                yield from self._notification_fields()
            yield Label(
                "密码只会写入权限受限的 Workspace secret 文件，不会显示在结果中。",
                id="resource-form-help",
            )
            yield Label("", id="resource-form-error")
            with Horizontal(classes="form-actions"):
                yield Button("取消", id="cancel")
                yield Button("保存", id="save", variant="primary")
        yield Footer()

    def _account_fields(self) -> ComposeResult:
        live = str(self.record.get("environment") or "paper") == "live"
        yield Label("账户类型")
        yield Select(
            (("模拟账户", "paper"), ("实盘账户", "live")),
            value="live" if live else "paper",
            allow_blank=False,
            id="account-mode",
        )
        yield Label("账户名称")
        yield Input(
            value=str(self.record.get("account_id") or "paper-main"), id="resource-id"
        )
        yield Label("交易产品")
        segments = self.record.get("segments") or (
            self.record.get("segment") or "spot",
        )
        yield Select(
            (("现货", "spot"), ("永续合约", "perpetual")),
            value=str(next(iter(segments), "spot")),
            allow_blank=False,
            id="account-segment",
        )
        yield Label("交易服务商（实盘）")
        yield Select(
            (("Binance", "binance"), ("OKX", "okx")),
            value=str(self.record.get("broker") or "binance"),
            allow_blank=False,
            id="account-provider",
        )
        yield Label("账户用途（实盘）")
        yield Select(
            (("只读", "readonly"), ("允许交易", "trade")),
            value=str(self.record.get("credential_role") or "readonly"),
            allow_blank=False,
            id="account-role",
        )
        yield Label("初始余额（模拟账户）")
        yield Input(value="USDT=100000", id="account-balance")
        yield Label("API Key（实盘；修改时留空可沿用）")
        yield Input(password=True, id="secret-primary")
        yield Label("API Secret（实盘；修改时留空可沿用）")
        yield Input(password=True, id="secret-secondary")
        yield Label("Passphrase（OKX）")
        yield Input(password=True, id="secret-tertiary")

    def _data_fields(self) -> ComposeResult:
        yield Label("连接名称")
        yield Input(
            value=str(self.record.get("credential_id") or "massive-readonly"),
            id="resource-id",
        )
        yield Label("API endpoint")
        yield Input(
            value=str(self.record.get("endpoint") or "https://api.massive.com"),
            id="endpoint",
        )
        yield Checkbox(
            "包含期权目录和行情",
            value="options" in (self.record.get("capabilities") or ()),
            id="include-options",
        )
        yield Label("Massive API Key（修改时留空可沿用）")
        yield Input(password=True, id="secret-primary")

    def _model_fields(self) -> ComposeResult:
        provider = str(self.record.get("provider") or "openai")
        yield Label("模型服务")
        yield Select(
            (
                ("OpenAI", "openai"),
                ("Anthropic", "anthropic"),
                ("OpenRouter", "openrouter"),
                ("Ollama", "ollama"),
                ("LM Studio", "lmstudio"),
                ("其他兼容服务", "custom"),
            ),
            value=provider
            if provider
            in {"openai", "anthropic", "openrouter", "ollama", "lmstudio", "custom"}
            else "custom",
            allow_blank=False,
            id="model-provider",
        )
        yield Label("连接名称")
        yield Input(
            value=str(self.record.get("connection_id") or f"{provider}-main"),
            id="resource-id",
        )
        yield Label("接口模式")
        yield Select(
            (
                ("OpenAI Responses", "openai-responses"),
                ("OpenAI Chat Completions", "openai-chat-completions"),
                ("Anthropic Messages", "anthropic-messages"),
                ("Ollama Native", "ollama-native"),
            ),
            value=str(self.record.get("api_mode") or "openai-responses"),
            allow_blank=False,
            id="model-mode",
        )
        yield Label("接口地址")
        yield Input(
            value=str(self.record.get("base_url") or "https://api.openai.com/v1"),
            id="endpoint",
        )
        yield Label("已知模型（逗号分隔，可留空后发现）")
        yield Input(
            value=",".join(str(value) for value in self.record.get("models") or ()),
            id="models",
        )
        yield Label("API Key（本机服务可留空；修改时留空可沿用）")
        yield Input(password=True, id="secret-primary")

    def _notification_fields(self) -> ComposeResult:
        provider = str(self.record.get("provider") or "feishu")
        yield Label("通知渠道")
        yield Select(
            (("飞书", "feishu"), ("Telegram", "telegram")),
            value=provider,
            allow_blank=False,
            id="notification-provider",
        )
        yield Label("通知名称")
        yield Input(
            value=str(self.record.get("destination_id") or f"{provider}-alerts"),
            id="resource-id",
        )
        yield Label("Webhook URL / Bot Token（修改时留空可沿用）")
        yield Input(password=True, id="secret-primary")
        yield Label("Telegram chat_id")
        yield Input(value=str(self.record.get("chat_id") or ""), id="chat-id")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.action_cancel()
            return
        self.query_one("#resource-form-error", Label).update("正在安全保存配置…")
        self.query_one("#save", Button).disabled = True
        self.run_worker(
            self._save,
            name="resource-save",
            group="resource-save",
            thread=True,
            exclusive=True,
            exit_on_error=False,
        )

    def _save(self) -> dict[str, Any]:
        if self.kind == "accounts":
            return self._save_account()
        if self.kind == "data":
            return self._save_data()
        if self.kind == "models":
            return self._save_model()
        return self._save_notification()

    def _save_account(self) -> dict[str, Any]:
        owner = self.app.state.owner  # type: ignore[attr-defined]
        application = AccountConfigurationApplication(owner)
        account_id = self._input("resource-id")
        mode = self._select("account-mode")
        segment = self._select("account-segment")
        if mode == "paper":
            if self.record:
                return application.modify(
                    account_id, segment=segment, environment="paper"
                )
            balance = self._input("account-balance")
            return application.simulate(
                account_id,
                segment=segment,
                initial_balances=(balance,) if balance else (),
            )
        provider = self._select("account-provider")
        role = self._select("account-role")
        credential_id = str(
            self.record.get("credential_id") or f"{account_id}-credential"
        )
        values = {
            "api_key": self._input("secret-primary"),
            "api_secret": self._input("secret-secondary"),
        }
        if provider == "okx":
            values["passphrase"] = self._input("secret-tertiary")
        if all(values.values()):
            CredentialConfigurationApplication(owner).configure_secret_values(
                credential_id,
                provider=provider,
                values=values,
                role=role,
                overwrite=bool(self.record),
            )
        elif not self.record:
            raise ValueError("实盘账户需要完整安全凭据")
        return application.connect(
            account_id,
            broker=provider,
            integration_provider=provider,
            segment=segment,
            environment="live",
            credential=credential_id,
            credential_role=role,
            force=bool(self.record),
        )

    def _save_data(self) -> dict[str, Any]:
        owner = self.app.state.owner  # type: ignore[attr-defined]
        credential_id = self._input("resource-id")
        secret = self._input("secret-primary")
        if secret:
            CredentialConfigurationApplication(owner).configure_secret_values(
                credential_id,
                provider="massive",
                values={"api_key": secret},
                overwrite=bool(self.record),
            )
        elif not self.record:
            raise ValueError("Massive 连接需要 API Key")
        capabilities = ["reference", "equity_market"]
        if self.query_one("#include-options", Checkbox).value:
            capabilities.append("options")
        return ReferenceProviderConfigurationApplication(owner).configure_massive(
            credential_id=credential_id,
            endpoint=self._input("endpoint"),
            capabilities=capabilities,
        )

    def _save_model(self) -> dict[str, Any]:
        owner = self.app.state.owner  # type: ignore[attr-defined]
        provider = self._select("model-provider")
        connection_id = self._input("resource-id")
        secret = self._input("secret-primary")
        credential_id: str | None = None
        if secret:
            credential_id = str(
                self.record.get("credential_id") or f"{connection_id}-auth"
            )
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
                overwrite=bool(self.record),
            )
        elif self.record.get("credential_id"):
            credential_id = str(self.record["credential_id"])
        return AgentResourceApplication(owner).configure_model_connection(
            connection_id,
            provider=provider,
            api_mode=self._select("model-mode"),
            base_url=self._input("endpoint"),
            credential_id=credential_id,
            models=tuple(
                value.strip()
                for value in self._input("models").split(",")
                if value.strip()
            ),
            overwrite=bool(self.record),
        )

    def _save_notification(self) -> dict[str, Any]:
        owner = self.app.state.owner  # type: ignore[attr-defined]
        provider = self._select("notification-provider")
        destination_id = self._input("resource-id")
        secret = self._input("secret-primary")
        credential_id = str(self.record.get("credential_id") or destination_id)
        field = "bot_token" if provider == "telegram" else "webhook_url"
        credentials = CredentialConfigurationApplication(owner)
        if secret:
            credential = credentials.configure_secret_values(
                credential_id,
                provider=provider,
                values={field: secret},
                role="notification-send",
                overwrite=bool(self.record),
            )
        elif self.record:
            credential = credentials.show(credential_id)
        else:
            raise ValueError("通知提醒需要 Webhook URL 或 Bot Token")
        raw = credential["secret_refs"]
        if not isinstance(raw, Mapping) or not isinstance(raw.get(field), Mapping):
            raise ValueError("通知凭据缺少 SecretRef")
        reference = raw[field]
        return NotificationAdminApplication(owner).configure(
            destination_id,
            provider=provider,  # type: ignore[arg-type]
            credential_id=credential_id,
            secret_ref=SecretRef(str(reference["source"]), str(reference["id"])),  # type: ignore[arg-type]
            chat_id=self._input("chat-id") or None,
        )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "resource-save":
            return
        self.query_one("#save", Button).disabled = False
        if event.state.name == "ERROR":
            self.query_one("#resource-form-error", Label).update(
                f"保存失败：{event.worker.error}"
            )
        elif event.state.name == "SUCCESS":
            result = event.worker.result
            self.dismiss(dict(result) if isinstance(result, dict) else {})

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _input(self, id_: str) -> str:
        return self.query_one(f"#{id_}", Input).value.strip()

    def _select(self, id_: str) -> str:
        value = self.query_one(f"#{id_}", Select).value
        if value is Select.NULL:
            raise ValueError("请选择所有必填项")
        return str(value)


def _label(kind: str) -> str:
    return {
        "accounts": "交易账户",
        "data": "市场数据",
        "models": "AI 模型",
        "notifications": "通知提醒",
    }[kind]
