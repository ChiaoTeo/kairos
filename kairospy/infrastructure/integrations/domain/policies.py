from __future__ import annotations

from .bindings import AccessScope, IntegrationBinding


def binding_is_public(binding: IntegrationBinding) -> bool:
    return binding.access is AccessScope.PUBLIC


__all__ = ["binding_is_public"]
