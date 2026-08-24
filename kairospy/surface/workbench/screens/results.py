"""Stable worker result kinds used by the Workbench presentation layer."""

from __future__ import annotations

from enum import StrEnum


class ResultKind(StrEnum):
    OBSERVE = "observe"
    MARKET = "market"
    MARKET_ROUTES = "market-routes"
    MARKET_OBSERVATION = "market-observation"
    MARKET_DATASETS = "market-datasets"
    MARKET_FILE = "market-file-result"
    WORKSPACE_MARKET = "workspace-market-result"
    REFERENCE_RELATED = "reference-related"
    KAIROS_COMMAND = "kairos-command"
    OPERATIONS_SERVICES = "operations-services"
    OPERATIONS = "operations-result"
    OPERATIONS_PROJECT = "operations-project-result"
    OPERATIONS_PROFILE = "operations-profile-result"
    BUSINESS = "business-result"
    ACCOUNT = "account-result"
    ORDER = "order-result"
    EXECUTION = "execution-result"
    LAUNCH_MARKET = "launch-market-result"
    RESOURCES_SUMMARY = "resources-summary"
    RESOURCE_WIZARD = "resource-wizard-result"
    RESEARCH = "research-result"
    STRATEGY_LAUNCHES = "strategy-launches"
    STRATEGY_INSTANCES = "strategy-instances"
    STRATEGY_COMPONENTS = "strategy-components"
    STRATEGY_INSTANCE = "strategy-instance-result"
    STRATEGY_TIMELINE = "strategy-timeline"
    STRATEGY_TIMELINE_EXPORT = "strategy-timeline-export"
    STRATEGY_ATTACH = "strategy-attach"
    STRATEGY = "strategy-result"
    STRATEGY_WIZARD = "strategy-wizard-result"


ResultKey = ResultKind | str


def parse_result_kind(value: str) -> ResultKey:
    """Return the closed kind when known and preserve scoped dynamic kinds."""

    try:
        return ResultKind(value)
    except ValueError:
        return value


__all__ = ["ResultKey", "ResultKind", "parse_result_kind"]
