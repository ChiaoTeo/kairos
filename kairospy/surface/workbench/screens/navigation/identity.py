"""Typed identities used by Workbench navigation control flow.

User-facing labels remain ordinary localized strings.  Only stable route identities
live here so product flows do not invent tuple spellings independently.
"""

from __future__ import annotations

from enum import StrEnum


NavigationContext = tuple[str, ...]


class Section(StrEnum):
    """Closed owners of top-level Workbench tasks."""

    PROJECT = "project"
    MARKET = "market"
    REFERENCE = "reference"
    STRATEGY = "strategy"
    ACCOUNT = "account"
    RESOURCES = "resources"
    RESEARCH = "research"
    OPERATIONS = "operations"


def route(section: Section, *pages: str) -> NavigationContext:
    """Build a route from a typed task owner and its owner-defined pages."""

    return (section.value, *pages)


class Routes:
    """Canonical identities for stable Workbench pages.

    Dynamic subjects such as a service name are appended to one of these prefixes;
    they are business selections, not additional page identities.
    """

    HOME: NavigationContext = ()
    PROJECT = route(Section.PROJECT)

    MARKET = route(Section.MARKET)
    MARKET_PROVIDERS = route(Section.MARKET, "providers")
    MARKET_INTENT = route(Section.MARKET, "intent")
    MARKET_MISSING = route(Section.MARKET, "missing")
    MARKET_NOT_FOUND = route(Section.MARKET, "not-found")
    MARKET_CATALOG_EXCHANGE = route(Section.MARKET, "catalog-exchange")
    MARKET_CATALOG_INSTRUMENT = route(Section.MARKET, "catalog-instrument")
    MARKET_CATALOG_SETUP = route(Section.MARKET, "catalog-setup")
    MARKET_LIVE = route(Section.MARKET, "live")
    MARKET_LIVE_UNAVAILABLE = route(Section.MARKET, "live-unavailable")
    MARKET_SELECTED = route(Section.MARKET, "selected")
    MARKET_RESULTS = route(Section.MARKET, "results")
    MARKET_WORKSPACE_MARKET_RESULTS = route(Section.MARKET, "workspace-market-results")
    MARKET_WORKSPACE_SUBSCRIPTIONS = route(Section.MARKET, "workspace-subscriptions")
    MARKET_WORKSPACE_SUBSCRIPTION_CONTENT = route(
        Section.MARKET, "workspace-subscription-content"
    )
    MARKET_WORKSPACE_SNAPSHOT_KIND = route(Section.MARKET, "workspace-snapshot-kind")
    MARKET_WORKSPACE_TIMEFRAME = route(Section.MARKET, "workspace-timeframe")
    MARKET_WORKSPACE_PROVIDERS = route(Section.MARKET, "workspace-providers")

    REFERENCE = route(Section.REFERENCE)
    REFERENCE_SOURCES = route(Section.REFERENCE, "sources")
    REFERENCE_SOURCE_SELECTED = route(Section.REFERENCE, "source-selected")
    REFERENCE_INSTRUMENT_TYPES = route(Section.REFERENCE, "instrument-types")
    REFERENCE_SELECTED = route(Section.REFERENCE, "selected")

    STRATEGY = route(Section.STRATEGY)
    STRATEGY_LAUNCHES = route(Section.STRATEGY, "launches")
    STRATEGY_SELECTED = route(Section.STRATEGY, "selected")
    STRATEGY_READINESS = route(Section.STRATEGY, "readiness")
    STRATEGY_SETUP = route(Section.STRATEGY, "setup")
    STRATEGY_INSTANCES = route(Section.STRATEGY, "instances")
    STRATEGY_INSTANCE = route(Section.STRATEGY, "instance")
    STRATEGY_ATTACH = route(Section.STRATEGY, "attach")
    STRATEGY_COMPONENTS = route(Section.STRATEGY, "components")
    STRATEGY_TIMELINE = route(Section.STRATEGY, "timeline")
    STRATEGY_EXECUTION = route(Section.STRATEGY, "execution")
    STRATEGY_MARKET = route(Section.STRATEGY, "market")

    ACCOUNT = route(Section.ACCOUNT)
    ACCOUNT_LIST = route(Section.ACCOUNT, "accounts")
    ACCOUNT_SELECTED = route(Section.ACCOUNT, "selected")
    ACCOUNT_FUNDS = route(Section.ACCOUNT, "funds")
    ACCOUNT_ORDERS = route(Section.ACCOUNT, "orders")
    ACCOUNT_ORDER_SEGMENTS = route(Section.ACCOUNT, "order-segments")
    ACCOUNT_TRANSFER_RESULT = route(Section.ACCOUNT, "transfer-result")

    RESOURCES = route(Section.RESOURCES)
    RESOURCES_SELECTED = route(Section.RESOURCES, "selected")
    RESOURCES_SETUP = route(Section.RESOURCES, "setup")
    RESOURCES_DATA = route(Section.RESOURCES, "data")
    RESOURCES_MODELS = route(Section.RESOURCES, "models")
    RESOURCES_MODEL_ENDPOINTS = route(Section.RESOURCES, "model_endpoints")
    RESOURCES_AI_MODELS = route(Section.RESOURCES, "ai-models")
    RESOURCES_MODEL_CHAT = route(Section.RESOURCES, "model-chat")

    RESEARCH = route(Section.RESEARCH)
    RESEARCH_DATA = route(Section.RESEARCH, "data")
    RESEARCH_WORKFLOW = route(Section.RESEARCH, "research")

    OPERATIONS = route(Section.OPERATIONS)
    OPERATIONS_OVERVIEW = route(Section.OPERATIONS, "overview")
    OPERATIONS_PROJECT = route(Section.OPERATIONS, "project")
    OPERATIONS_CONFIG = route(Section.OPERATIONS, "config")
    OPERATIONS_PROFILES = route(Section.OPERATIONS, "profiles")
    OPERATIONS_BUSINESS = route(Section.OPERATIONS, "business")
    OPERATIONS_INSTANCES = route(Section.OPERATIONS, "instances")
    OPERATIONS_SERVICES = route(Section.OPERATIONS, "services")
    OPERATIONS_SERVICE = route(Section.OPERATIONS, "service")
    OPERATIONS_SERVICE_LOGS = route(Section.OPERATIONS, "service-logs")
    OPERATIONS_SUPPORTS = route(Section.OPERATIONS, "supports")
    OPERATIONS_SUPPORT = route(Section.OPERATIONS, "support")


def belongs_to(context: NavigationContext, section: Section) -> bool:
    """Return whether a route belongs to a typed top-level task."""

    return bool(context) and context[0] == section.value


def starts_with(context: NavigationContext, prefix: NavigationContext) -> bool:
    """Match a canonical page prefix, including routes with a dynamic subject."""

    return context[: len(prefix)] == prefix


__all__ = [
    "NavigationContext",
    "Routes",
    "Section",
    "belongs_to",
    "route",
    "starts_with",
]
