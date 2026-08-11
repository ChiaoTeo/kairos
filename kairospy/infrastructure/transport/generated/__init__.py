"""Python package boundary for FlatBuffers-generated modules.

``flatc --python`` emits absolute imports rooted at ``kairos``. The
application keeps generated code under its own namespace, so register the
generated package under the name expected by those modules before any table
module is imported.
"""

from __future__ import annotations

import sys

from . import kairos as _kairos

sys.modules.setdefault("kairos", _kairos)

