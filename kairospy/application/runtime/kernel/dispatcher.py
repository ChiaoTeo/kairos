from __future__ import annotations


def phase_for_domain(domain: object) -> str:
    if domain == "execution":
        return "order"
    return str(domain)


def hook_for_domain(domain: object) -> str:
    hooks = {
        "account": "on_account",
        "clock": "on_clock",
        "execution": "on_order",
        "system": "on_system",
    }
    return hooks.get(str(domain), "on_system")


__all__ = ["hook_for_domain", "phase_for_domain"]
