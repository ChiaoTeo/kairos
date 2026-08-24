class StrategySdkError(Exception):
    """Base error for stable strategy SDK failures."""


class ApplicationUnavailableError(StrategySdkError):
    pass


class StaleCurrentViewError(StrategySdkError):
    pass


class ContractDecodeError(StrategySdkError):
    pass


class UnsupportedContractVersionError(ContractDecodeError):
    pass


class UnsupportedContractValueError(ContractDecodeError):
    pass


class StrategyStateError(StrategySdkError):
    pass


class StrategyStateTypeError(StrategyStateError):
    def __init__(
        self,
        *,
        strategy_id: str,
        instance_id: str,
        key: str,
        expected: str,
        actual: str,
    ) -> None:
        super().__init__(
            f"strategy state type mismatch: strategy={strategy_id!r}, "
            f"instance={instance_id!r}, key={key!r}, expected={expected}, actual={actual}"
        )
        self.strategy_id = strategy_id
        self.instance_id = instance_id
        self.key = key
        self.expected = expected
        self.actual = actual


class StrategyCommandRejectedError(StrategySdkError):
    pass
