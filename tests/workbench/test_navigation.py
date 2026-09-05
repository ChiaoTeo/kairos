from __future__ import annotations

import pytest

from kairospy.surface.workbench.screens.session import (
    AccountSession,
    GuidedSession,
    MarketSession,
)
from kairospy.surface.workbench.screens.navigation import (
    Routes,
    back_targets,
    command_context,
    context_label,
    go_back,
)
from kairospy.surface.workbench.screens.selection import SelectionRecord
from kairospy.surface.workbench.screens.selection import LaunchRecordView
from kairospy.surface.workbench.screens.flows.resources.wizard import (
    ResourceWizardState,
)


class _NonCopyableOwnerRecord:
    def __deepcopy__(self, memo: object) -> object:
        raise AssertionError("owner contract records must not be copied by navigation")


@pytest.mark.parametrize(
    ("context", "parent"),
    [
        (("market", "live"), ("market",)),
        (("market", "live-unavailable"), ("market",)),
        (("reference",), ("market",)),
        (("operations", "service", "market"), ("operations", "services")),
        (("operations", "support", "aeron"), ("operations", "supports")),
        (("account", "orders"), ("account", "selected")),
        (("research", "data"), ("research",)),
        (("strategy", "execution"), ("strategy", "components")),
        (("strategy", "components"), ("strategy", "instance")),
        (("strategy", "instance"), ("strategy", "instances")),
    ],
)
def test_nested_contexts_have_one_central_parent(
    context: tuple[str, ...], parent: tuple[str, ...]
) -> None:
    session = GuidedSession(context=context)

    assert back_targets(session)[0] == parent
    assert go_back(session)
    assert session.context == parent


def test_home_has_no_parent() -> None:
    session = GuidedSession()

    assert not go_back(session)
    assert session.context == ()


def test_back_targets_follow_semantic_parents_to_home() -> None:
    session = GuidedSession(context=("strategy", "execution"))

    assert back_targets(session) == (
        ("strategy", "components"),
        ("strategy", "instance"),
        ("strategy", "instances"),
        ("strategy", "selected"),
        ("strategy",),
        (),
    )


def test_navigation_stack_returns_by_actual_entry_path() -> None:
    session = GuidedSession()
    session.enter("operations", "instances")
    session.enter("strategy", "instance")

    assert back_targets(session) == (("operations", "instances"), ())
    assert go_back(session)
    assert session.context == ("operations", "instances")
    assert go_back(session)
    assert session.context == ()


def test_home_clears_visited_page_stack() -> None:
    session = GuidedSession()
    session.enter("market")
    session.enter("reference")

    session.home()

    assert session.context == ()
    assert tuple(frame.context for frame in session.navigation_stack) == ((),)


def test_return_to_discards_descendants_without_guessing_a_parent() -> None:
    session = GuidedSession()
    session.enter("market")
    session.enter("market", "results")
    session.enter("market", "selected")
    session.enter("market", "providers")

    assert session.return_to("market", "selected")
    assert session.context == ("market", "selected")
    assert tuple(frame.context for frame in session.navigation_stack) == (
        (),
        ("market",),
        ("market", "results"),
        ("market", "selected"),
    )


def test_back_targets_do_not_copy_or_mutate_owner_contract_records() -> None:
    owner_record = _NonCopyableOwnerRecord()
    visible = SelectionRecord("market:test", "BTCUSDT", "binance", owner_record)
    session = GuidedSession(
        context=("market", "selected"),
        visible_records=(visible,),
        market=MarketSession(selected=owner_record, records=(visible,)),
    )

    assert back_targets(session) == (
        ("market", "results"),
        ("market",),
        (),
    )
    assert session.context == ("market", "selected")
    assert session.market.selected is owner_record
    assert session.market.records == (visible,)
    assert session.visible_records == (visible,)


def test_multi_segment_order_back_returns_through_segment_selection() -> None:
    session = GuidedSession(
        context=("account", "orders"),
        account=AccountSession(
            selected={"account_id": "main", "segments": ["spot", "usd_m_futures"]},
            selected_segment="usd_m_futures",
        ),
    )

    assert go_back(session)
    assert session.context == ("account", "order-segments")
    assert session.account.selected_segment == "usd_m_futures"

    assert go_back(session)
    assert session.context == ("account", "selected")
    assert session.account.selected_segment is None


def test_context_labels_are_derived_from_the_same_navigation_context() -> None:
    assert context_label(("market", "missing")) == "首页 / 市场与标的 / 标的查询状态"
    assert context_label(("market", "not-found")) == "首页 / 市场与标的 / 已覆盖范围内未找到"
    assert context_label(()) == "首页"
    assert context_label((), "trader") == "trader"
    assert context_label(("project",), "trader") == "trader / 项目管理"
    assert context_label(("market", "selected")) == "首页 / 市场与标的 / 已选标的"
    assert (
        context_label(("operations", "support", "system-supervisor"))
        == "首页 / 运行中心 / System Supervisor"
    )


def test_command_context_keeps_launch_anchor_across_resource_repair() -> None:
    session = GuidedSession(root_label="trader")
    session.strategy.selected_record = LaunchRecordView(
        {"launch_id": "paper-demo", "mode": "paper"}
    )
    session.enter_context(Routes.STRATEGY_READINESS)
    session.resources.kind = "data"
    session.resources.wizard = ResourceWizardState(
        "data", answers={"data-provider": "massive"}
    )
    session.enter_context(Routes.RESOURCES_SETUP)

    assert command_context(session).segments == (
        "paper-demo",
        "行情连接",
        "Massive",
    )
    assert back_targets(session)[0] == Routes.STRATEGY_READINESS


def test_command_context_keeps_market_query_across_catalog_connection_setup() -> None:
    session = GuidedSession(root_label="trader")
    session.market.query = "AAPL"
    session.enter_context(Routes.MARKET_CATALOG_SETUP)
    session.resources.kind = "data"
    session.resources.wizard = ResourceWizardState(
        "data", answers={"data-provider": "massive"}
    )
    session.enter_context(Routes.RESOURCES_SETUP)

    assert command_context(session).segments == ("AAPL", "行情连接", "Massive")
    assert back_targets(session)[0] == Routes.MARKET_CATALOG_SETUP
