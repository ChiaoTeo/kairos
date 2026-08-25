from __future__ import annotations

import pytest

from kairospy.surface.workbench.screens.session import GuidedSession
from kairospy.surface.workbench.screens.navigation import context_label, go_back


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


def test_context_labels_are_derived_from_the_same_navigation_context() -> None:
    assert context_label(()) == "首页"
    assert context_label(("market", "selected")) == "首页 / 市场行情 / 已选标的"
    assert (
        context_label(("operations", "support", "system-supervisor"))
        == "首页 / 运行中心 / System Supervisor"
    )
