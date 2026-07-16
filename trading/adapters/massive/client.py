from __future__ import annotations

from dataclasses import dataclass, replace
import json
import time
from typing import Callable, Mapping, Protocol
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

from .config import MassiveConfig, _ALLOWED_REST_HOST


_REWRITABLE_UPSTREAM_HOSTS = {"api.massive.com", "api.polygon.io"}


class MassiveError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MassiveResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    attempts: int = 1

    def json(self) -> dict[str, object] | list[object]:
        value = json.loads(self.body)
        if not isinstance(value, (dict, list)):
            raise MassiveError("Massive response must be a JSON object or array")
        return value


class MassiveTransport(Protocol):
    def request(self, url: str, headers: Mapping[str, str], timeout: int) -> MassiveResponse: ...


class UrllibMassiveTransport:
    def request(self, url: str, headers: Mapping[str, str], timeout: int) -> MassiveResponse:
        request = Request(url, headers=dict(headers))
        try:
            with urlopen(request, timeout=timeout) as response:
                return MassiveResponse(response.status, dict(response.headers.items()), response.read())
        except HTTPError as error:
            return MassiveResponse(error.code, dict(error.headers.items()), error.read())


class MassiveClient:
    def __init__(
        self,
        config: MassiveConfig,
        transport: MassiveTransport | None = None,
        *,
        wait: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.transport = transport or UrllibMassiveTransport()
        self.wait = wait

    def get(self, path_or_url: str, params: Mapping[str, object] | None = None) -> MassiveResponse:
        url = self._url(path_or_url, params)
        for attempt in range(self.config.max_retries):
            response = self.transport.request(
                url,
                {"Authorization": f"Bearer {self.config.api_key}", "User-Agent": "trader-massive/1.0"},
                self.config.timeout_seconds,
            )
            if 200 <= response.status < 300:
                return replace(response, attempts=attempt + 1)
            if response.status not in {408, 429, 500, 502, 503, 504}:
                raise MassiveError(f"Massive request failed status={response.status} url={redact_url(url)}")
            if attempt + 1 < self.config.max_retries:
                retry_after = response.headers.get("Retry-After")
                self.wait(float(retry_after) if retry_after else 1.5 * (attempt + 1))
        raise MassiveError(f"Massive request exhausted retries url={redact_url(url)}")

    def pages(self, path: str, params: Mapping[str, object] | None = None, *, max_pages: int = 100_000):
        next_url: str | None = path
        next_params = params
        seen: set[str] = set()
        for _ in range(max_pages):
            if next_url is None:
                return
            response = self.get(next_url, next_params)
            payload = response.json()
            if isinstance(payload, list):
                payload = {"status": "OK", "results": payload}
            request_id = str(payload.get("request_id", ""))
            identity = request_id or redact_url(self._url(next_url, next_params))
            if identity in seen:
                raise MassiveError(f"Massive pagination cycle detected request={identity}")
            seen.add(identity)
            yield response, payload
            raw_next = payload.get("next_url")
            next_url = str(raw_next) if raw_next else None
            next_params = None
        raise MassiveError(f"Massive pagination exceeded max_pages={max_pages}")

    def _url(self, path_or_url: str, params: Mapping[str, object] | None) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            url = path_or_url
        else:
            url = urljoin(self.config.rest_base.rstrip("/") + "/", path_or_url.lstrip("/"))
        parsed = urlparse(url)
        if parsed.hostname in _REWRITABLE_UPSTREAM_HOSTS:
            parsed = parsed._replace(scheme="https", netloc=_ALLOWED_REST_HOST)
        if parsed.hostname != _ALLOWED_REST_HOST:
            raise MassiveError("refusing Massive request outside api.massiveprivateserver.site")
        if parsed.scheme != "https":
            parsed = parsed._replace(scheme="https")
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        if params:
            query.update({key: str(value).lower() if isinstance(value, bool) else str(value) for key, value in params.items() if value is not None})
        query.pop("apiKey", None)
        return urlunparse(parsed._replace(query=urlencode(query)))


def redact_url(url: str) -> str:
    parsed = urlparse(url)
    query = [(key, "***" if key.lower() in {"apikey", "api_key", "token"} else value) for key, value in parse_qsl(parsed.query)]
    return urlunparse(parsed._replace(query=urlencode(query)))
