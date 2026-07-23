from __future__ import annotations

from typing import Iterable


def acquisition_request_evidence(request: object) -> dict[str, object]:
    """Stable lineage evidence for the acquisition selection that built a release."""
    instruments = _strings(getattr(request, "instruments", ()))
    fields = _strings(getattr(request, "fields", ()))
    payload: dict[str, object] = {
        "dataset": str(getattr(request, "logical_key")),
        "time_ranges": request_windows_evidence(request),
        "instruments": list(instruments),
    }
    if fields:
        payload["fields"] = list(fields)
    return payload


def request_windows_evidence(request: object) -> list[dict[str, object]]:
    return [
        {"start": item.start.isoformat(), "end": item.end.isoformat(), "boundary": "[start,end)"}
        for item in tuple(getattr(request, "missing"))
    ]


def universe_evidence(
    request: object,
    *,
    symbols: Iterable[str],
    observed_instruments: Iterable[str],
    selection_source: str,
    scope: str | None = None,
    completeness: str | None = None,
    max_observed_instruments: int = 100,
) -> dict[str, object]:
    """Describe the universe actually represented by a release.

    This intentionally stays as a small dictionary helper. It is release
    evidence, not a product-policy framework.
    """
    requested = _strings(getattr(request, "instruments", ()))
    observed = _strings(observed_instruments)
    resolved_scope = scope or ("bounded" if requested else "full_market")
    normalized_scope = "full_market" if resolved_scope in {"full-market", "full_market"} else resolved_scope
    legacy_kind = "full-market" if normalized_scope == "full_market" else normalized_scope
    resolved_completeness = completeness or ("partial" if normalized_scope == "bounded" else "complete_or_best_effort")
    payload: dict[str, object] = {
        "kind": legacy_kind,
        "scope": normalized_scope,
        "symbols": list(_strings(symbols)),
        "selection": selection_source,
        "selection_source": selection_source,
        "requested_instruments": list(requested),
        "observed_instruments_count": len(observed),
        "completeness": resolved_completeness,
    }
    if normalized_scope == "bounded" or len(observed) <= max_observed_instruments:
        payload["observed_instruments"] = list(observed)
    elif observed:
        payload["observed_instruments_sample"] = list(observed[:max_observed_instruments])
    return payload


def _strings(values: Iterable[object]) -> tuple[str, ...]:
    return tuple(str(item).strip() for item in values if str(item).strip())
