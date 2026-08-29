"""Stable worker result kinds used by the Workbench presentation layer."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ResultKind(StrEnum):
    CONFIRMED = "confirmed"
    OBSERVE = "observe"
    MARKET = "market"
    MARKET_ROUTES = "market-routes"
    MARKET_OBSERVATION = "market-observation"
    MARKET_DATASETS = "market-datasets"
    MARKET_FILE = "market-file-result"
    WORKSPACE_MARKET = "workspace-market-result"
    REFERENCE_RELATED = "reference-related"
    REFERENCE_STATUS = "reference-status"
    KAIROS_COMMAND = "kairos-command"
    OPERATIONS_SERVICES = "operations-services"
    OPERATIONS_OVERVIEW = "operations-overview"
    OPERATIONS = "operations-result"
    OPERATIONS_PROJECT = "operations-project-result"
    OPERATIONS_PROFILE = "operations-profile-result"
    BUSINESS = "business-result"
    ACCOUNT = "account-result"
    ORDER = "order-result"
    TRANSFER = "transfer-result"
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
    REFERENCE_RECORDS = "reference-records"
    RESOURCE_LIST = "resource-list"
    RESOURCE_ACTION = "resource-action"
    MARKET_DIAGNOSTIC = "market-diagnostic"
    MARKET_CATALOG_SETUP = "market-catalog-setup"
    MARKET_CATALOG_PREPARE = "market-catalog-prepare"


@dataclass(frozen=True, slots=True)
class ResultRoute:
    """Closed result category plus a validated product-specific qualifier."""

    kind: ResultKind
    qualifier: str | None = None


__all__ = ["ResultKind", "ResultRoute"]
