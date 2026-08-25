from __future__ import annotations

import pytest

from kairospy.surface.workbench.screens.session import (
    AccountSession,
    GuidedSession,
    ResourcesSession,
)
from kairospy.surface.workbench.screens.navigation import (
    back_targets,
    context_label,
    go_back,
)


@pytest.mark.parametrize(
    ("context", "parent"),
    [
        (("market", "connected"), ("market",)),
        (("operations", "service", "market"), ("operations", "services")),
        (("operations", "support", "aeron"), ("operations", "supports")),
        (("resources", "account-orders"), ("resources", "account-operations")),
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


def test_multi_segment_order_back_returns_through_segment_selection() -> None:
    session = GuidedSession(
        context=("resources", "account-orders"),
        resources=ResourcesSession(
            kind="accounts",
            selected={"account_id": "main", "segments": ["spot", "usd_m_futures"]},
        ),
        account=AccountSession(selected_segment="usd_m_futures"),
    )

    assert go_back(session)
    assert session.context == ("resources", "account-order-segments")
    assert session.account.selected_segment == "usd_m_futures"

    assert go_back(session)
    assert session.context == ("resources", "account-operations")
    assert session.account.selected_segment is None


def test_context_labels_are_derived_from_the_same_navigation_context() -> None:
    assert context_label(()) == "首页"
    assert context_label((), "trader") == "trader"
    assert context_label(("project",), "trader") == "trader / 项目管理"
    assert context_label(("market", "selected")) == "首页 / 市场行情 / 已选标的"
    assert (
        context_label(("operations", "support", "system-supervisor"))
        == "首页 / 运行中心 / System Supervisor"
    )
