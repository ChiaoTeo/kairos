from __future__ import annotations

import asyncio
from typing import Any

import pytest
from textual.widgets import RichLog, Static

from kairospy.surface.workbench import KairosWorkbenchApp
from kairospy.surface.workbench.screens.command_line import CommandLineScreen
from kairospy.surface.workbench.screens.flows.resources import account
from kairospy.surface.workbench.screens.flows.resources import (
    configuration as resources,
)
from kairospy.surface.workbench.screens.flows.resources.account_transfers import (
    TransferPromptState,
    transfer_available,
)
from kairospy.surface.workbench.screens.flows.resources import account_transfers
from kairospy.surface.workbench.widgets import (
    ConfirmInteraction,
    InputInteraction,
    renderable_plain_text,
)

from app_support import log_text, workbench_state


def record(*, capabilities: list[str]) -> dict[str, Any]:
    return {
        "account_id": "main",
        "broker": "binance",
        "integration_provider": "binance",
        "environment": "live",
        "segments": ["funding"],
        "credential_role": "transfer" if "transfer" in capabilities else "readonly",
        "capabilities": capabilities,
        "status": "configured",
    }


def preview_result(prompt: TransferPromptState) -> dict[str, Any]:
    return {
        "owner": "capital",
        "mode": "standalone",
        "scope": "direct-provider",
        "status": "confirmation_required",
        "preview": {
            "preview_id": "preview-1",
            "plan_id": "manual-transfer-1",
            "idempotency_key": prompt.idempotency_key,
            "route_id": "standalone-direct-transfer",
            "route_version": 1,
            "route_kind": "internal_transfer",
            "source": {
                "broker": "binance",
                "account_id": "main",
                "segment": "funding",
                "asset": "USDT",
            },
            "destination": {
                "broker": "binance",
                "account_id": "main",
                "segment": "spot",
                "asset": "USDT",
            },
            "amount": "10",
            "source_authority": "standalone-explicit-confirmation",
            "source_account_watermark": 7,
            "destination_account_watermark": 8,
            "source_observed_available": "100",
            "destination_observed_available": "20",
            "created_at": 100,
            "expires_at": 200,
        },
        "source_available_after": "90",
        "destination_available_after": "30",
    }


def confirmed_result() -> dict[str, Any]:
    return {
        "owner": "capital",
        "mode": "standalone",
        "scope": "direct-provider",
        "status": "awaitingtransfer",
        "result_unknown": True,
        "plan": {
            "plan_id": "manual-transfer-1",
            "preview_id": "preview-1",
            "idempotency_key": "key-1",
            "source": {
                "broker": "binance",
                "account_id": "main",
                "segment": "funding",
                "asset": "USDT",
            },
            "destination": {
                "broker": "binance",
                "account_id": "main",
                "segment": "spot",
                "asset": "USDT",
            },
            "amount": "10",
            "status": "awaitingtransfer",
        },
        "operation": {
            "operation_id": "operation-1",
            "status": "indeterminate",
            "participant_operation_id": None,
            "participant_state": None,
            "failure_reason": "participant result is unknown",
            "attempt_count": 1,
        },
    }


def test_transfer_capability_requires_effective_transfer_permission() -> None:
    assert transfer_available(record(capabilities=["read", "transfer"]))
    assert not transfer_available(record(capabilities=["read"]))
    controlled = record(capabilities=["read"])
    controlled["capital_controller_account_id"] = "controller"
    assert transfer_available(controlled)


def test_transfer_prompt_validates_exact_positive_amount() -> None:
    prompt = TransferPromptState(record(capabilities=["read", "transfer"]))
    prompt.accept("destination-account", "")
    prompt.accept("destination-segment", "spot")
    prompt.accept("asset", "usdt")
    with pytest.raises(ValueError, match="大于 0"):
        prompt.accept("amount", "0")
    prompt.accept("amount", "10.2500")
    assert prompt.values["asset"] == "USDT"
    assert prompt.values["amount"] == "10.2500"
    assert prompt.source_segment == "funding"


def test_cross_account_binding_uses_member_reads_and_controller_transfer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str]] = []

    def run(_self: Any, arguments: tuple[str, ...]) -> dict[str, Any]:
        assert arguments[0] == "trading-binding"
        account_id = arguments[arguments.index("--account-id") + 1]
        segment = arguments[arguments.index("--segment") + 1]
        access = arguments[arguments.index("--access") + 1]
        calls.append((account_id, segment, access))
        controller = "main" if account_id != "main" else None
        return {
            "account_id": account_id,
            "remote_account_id": account_id,
            "broker": "binance",
            "integration_adapter": "binance",
            "environment": "live",
            "segment_key": segment,
            "provider_segment": segment,
            "credential_id": f"{account_id}-{access}",
            "credential_role": access,
            "base_url": "https://api.binance.com",
            "capital_controller_account_id": controller,
            "participant_account_ref": f"{account_id}@example.com",
        }

    monkeypatch.setattr(
        account_transfers.AccountCliApplication,
        "run",
        run,
    )
    monkeypatch.setattr(account_transfers, "_first_account_segment", lambda *_: "spot")
    selected = record(capabilities=["read"])
    selected.update(
        account_id="sub-a",
        segments=["spot"],
        capital_controller_account_id="main",
    )
    prompt = TransferPromptState(selected)
    prompt.values.update(
        {
            "destination-account": "sub-b",
            "destination-segment": "spot",
            "asset": "USDT",
            "amount": "10",
        }
    )
    state = workbench_state()
    binding = account_transfers._binding(state, prompt)  # noqa: SLF001

    assert calls == [
        ("sub-a", "spot", "read"),
        ("sub-b", "spot", "read"),
        ("main", "spot", "transfer"),
    ]
    assert binding["controller"]["account_id"] == "main"


def test_readonly_account_shows_capability_degradation_without_amount_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resources,
        "list_records",
        lambda state, kind: (record(capabilities=["read"]),),
    )

    async def run() -> tuple[str, object]:
        app = KairosWorkbenchApp(workbench_state())
        async with app.run_test(size=(120, 32)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("4", "1", "1", "7"):
                screen.submit(value)
                await pilot.pause(0.1)
            return (
                log_text(screen.query_one("#command-output", RichLog)),
                screen.session.interaction,
            )

    output, interaction = asyncio.run(run())
    assert "资金划转不可用" in output
    assert "仅允许读取" in output
    assert not isinstance(interaction, InputInteraction)


def test_authorized_transfer_requires_preview_and_explicit_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = record(capabilities=["read", "transfer"])
    monkeypatch.setattr(resources, "list_records", lambda state, kind: (selected,))
    calls: list[str] = []

    def fake_preview(state: Any, prompt: TransferPromptState) -> dict[str, Any]:
        calls.append("preview")
        return preview_result(prompt)

    def fake_confirm(state: Any, prompt: TransferPromptState) -> dict[str, Any]:
        calls.append("confirm")
        assert prompt.preview is not None
        return confirmed_result()

    monkeypatch.setattr(account, "preview_transfer", fake_preview)
    monkeypatch.setattr(account, "confirm_transfer", fake_confirm)

    async def run() -> tuple[list[str], str, str, str]:
        app = KairosWorkbenchApp(workbench_state())
        async with app.run_test(size=(120, 36)) as pilot:
            screen = app.screen
            assert isinstance(screen, CommandLineScreen)
            for value in ("4", "1", "1", "7", "", "spot", "", "10"):
                screen.submit(value)
                await pilot.pause(0.1)
            assert isinstance(screen.session.interaction, ConfirmInteraction)
            before = list(calls)
            screen.submit("/confirm")
            await pilot.pause(0.1)
            output = log_text(screen.query_one("#command-output", RichLog))
            status = str(screen.query_one("#command-status", Static).render())
            context = str(screen.query_one("#command-context", Static).render())
            return before, output, status, context

    before, output, status, context = asyncio.run(run())
    assert before == ["preview"]
    assert calls == ["preview", "confirm"]
    assert "资金划转预览" in output
    assert "不要重新提交" in output
    assert status == "划转结果未知 · 请查询本次状态"
    assert "资金划转" in context


def test_transfer_unknown_result_rendering_directs_safe_status_query() -> None:
    body = account._transfer_result_renderable(  # noqa: SLF001
        confirmed_result(), title="资金划转结果"
    )
    text = renderable_plain_text(body)
    assert "结果未知" in text
    assert "查询本次状态" in text
    assert "不要重新提交" in text
    assert "operation_id" not in text
