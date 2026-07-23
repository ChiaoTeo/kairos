from __future__ import annotations

from kairospy.integrations.config import CcxtExchangeSettings

from .errors import CcxtDependencyUnavailable


def build_ccxt_exchange(settings: CcxtExchangeSettings):
    try:
        import ccxt
    except ImportError as error:
        raise CcxtDependencyUnavailable("install CCXT support with `uv sync --extra crypto`") from error
    try:
        exchange_type = getattr(ccxt, normalized_ccxt_exchange_id(settings.exchange_id))
    except AttributeError as error:
        raise ValueError(f"unsupported CCXT exchange id: {settings.exchange_id}") from error
    exchange = exchange_type(_exchange_config(settings))
    if settings.sandbox:
        sandbox = getattr(exchange, "set_sandbox_mode", None)
        if callable(sandbox):
            sandbox(True)
    return exchange


def build_ccxt_pro_exchange(settings: CcxtExchangeSettings):
    try:
        import ccxt.pro as ccxtpro
    except ImportError as error:
        raise CcxtDependencyUnavailable("install CCXT Pro support with `uv sync --extra crypto`") from error
    try:
        exchange_type = getattr(ccxtpro, normalized_ccxt_exchange_id(settings.exchange_id))
    except AttributeError as error:
        raise ValueError(f"unsupported CCXT Pro exchange id: {settings.exchange_id}") from error
    exchange = exchange_type(_exchange_config(settings))
    if settings.sandbox:
        sandbox = getattr(exchange, "set_sandbox_mode", None)
        if callable(sandbox):
            sandbox(True)
    return exchange


def _exchange_config(settings: CcxtExchangeSettings) -> dict[str, object]:
    config: dict[str, object] = {
        "enableRateLimit": settings.enable_rate_limit,
        "options": settings.options or {},
    }
    if settings.api_key is not None:
        config["apiKey"] = settings.api_key
    if settings.api_secret is not None:
        config["secret"] = settings.api_secret
    if settings.password is not None:
        config["password"] = settings.password
    if settings.uid is not None:
        config["uid"] = settings.uid
    if settings.timeout_ms is not None:
        config["timeout"] = settings.timeout_ms
    return config


def normalized_ccxt_exchange_id(value: str) -> str:
    exchange_id = value.strip().lower()
    return {"okex": "okx"}.get(exchange_id, exchange_id)
