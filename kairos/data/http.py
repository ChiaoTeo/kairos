from __future__ import annotations

import json
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def download(url: str, params: dict[str, object] | None = None, retries: int = 4) -> bytes:
    request = Request(f"{url}?{urlencode(params)}" if params else url, headers={"User-Agent": "kairos-data-pipeline/1.0"})
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=30) as response:
                return response.read()
        except HTTPError as error:
            if 400 <= error.code < 500 and error.code != 429:
                raise
            if attempt + 1 == retries:
                raise
            time.sleep(1.5 * (attempt + 1))
        except Exception:
            if attempt + 1 == retries:
                raise
            time.sleep(1.5 * (attempt + 1))


def download_json(url: str, params: dict[str, object]) -> dict[str, object]:
    return json.loads(download(url, params))
