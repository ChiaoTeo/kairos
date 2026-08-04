from __future__ import annotations

from dataclasses import dataclass

from kairospy.application.support.runtime.domain.components import RuntimeComponents


@dataclass(frozen=True, slots=True)
class SystemComponents:
    """Business capabilities installed in one system instance."""

    market: object | None = None
    account: object | None = None
    account_catalog: object | None = None
    execution: object | None = None
    reference: object | None = None
    strategy: object | None = None

    @classmethod
    def from_runtime(cls, components: RuntimeComponents | None) -> "SystemComponents":
        runtime = components or RuntimeComponents()
        return cls(
            market=runtime.market,
            account=runtime.account,
            account_catalog=runtime.account_catalog,
            execution=runtime.execution,
            reference=runtime.reference,
        )

    def runtime_components(self) -> RuntimeComponents:
        return RuntimeComponents(
            market=self.market,
            account=self.account,
            account_catalog=self.account_catalog,
            execution=self.execution,
            reference=self.reference,
        )

    def get(self, name: str) -> object | None:
        if name not in {"market", "account", "account_catalog", "execution", "reference", "strategy"}:
            raise KeyError(f"unknown system component: {name}")
        return getattr(self, name)

    def require(self, name: str) -> object:
        component = self.get(name)
        if component is None:
            raise LookupError(f"system component is not installed: {name}")
        return component

    def names(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                name
                for name in ("market", "account", "account_catalog", "execution", "reference", "strategy")
                if getattr(self, name, None) is not None
            )
        )


__all__ = ["SystemComponents"]
