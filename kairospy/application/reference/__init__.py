"""Public Reference control and query application boundary."""

from __future__ import annotations

from ..system.clients import ReferenceSystemClient


# Compatibility name for callers that still import the old facade.  Process
# control and business endpoint access now belong to System clients.
ReferenceControlApplication = ReferenceSystemClient


from .cli import ReferenceCliApplication


__all__ = ["ReferenceCliApplication", "ReferenceControlApplication"]
