"""Python-facing investment subsystem."""

from .application import InvestmentApplication
from .composition import compose_investment_application

__all__ = ["InvestmentApplication", "compose_investment_application"]
