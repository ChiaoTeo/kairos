from __future__ import annotations

import pytest

from kairospy.surface.cli.interactive.models import InteractiveContext


@pytest.fixture
def interactive_context() -> InteractiveContext:
    return InteractiveContext(owner=None, snapshot=None, workspace_arg=None)
