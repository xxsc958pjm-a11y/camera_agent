from __future__ import annotations

from urllib.parse import urljoin, urlparse

import requests

from .exceptions import PTUConnectionError
from .models import PTUConfig


class PTUWebClient:
    def __init__(self, config: PTUConfig):
        self.config = config
        self.session = requests.Session()

    def build_url(self, path_or_url: str) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            return path_or_url
        return urljoin(f"{self.config.base_url}/", path_or_url.lstrip("/"))

    def get(self, path_or_url: str = "/", params: dict | None = None) -> requests.Response:
        url = self.build_url(path_or_url)
        try:
            response = self.session.get(
                url,
                params=params,
                timeout=self.config.timeout_sec,
                verify=self.config.verify_http,
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            raise PTUConnectionError(f"HTTP GET failed for {url}: {exc}") from exc

    def fetch_root_page(self) -> requests.Response:
        return self.get("/")

    def post(
        self,
        path_or_url: str,
        data: str | dict | None = None,
        params: dict | None = None,
    ) -> requests.Response:
        url = self.build_url(path_or_url)
        try:
            response = self.session.post(
                url,
                data=data,
                params=params,
                timeout=self.config.timeout_sec,
                verify=self.config.verify_http,
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            raise PTUConnectionError(f"HTTP POST failed for {url}: {exc}") from exc

    def fetch_text(self, path_or_url: str = "/", params: dict | None = None) -> str:
        return self.get(path_or_url, params=params).text

    def fetch_bytes(self, path_or_url: str = "/") -> bytes:
        return self.get(path_or_url).content

    def post_text(
        self,
        path_or_url: str,
        data: str | dict | None = None,
        params: dict | None = None,
    ) -> str:
        return self.post(path_or_url, data=data, params=params).text

    def is_same_origin(self, path_or_url: str) -> bool:
        candidate = self.build_url(path_or_url)
        return urlparse(candidate).netloc == urlparse(self.config.base_url).netloc
