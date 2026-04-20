from __future__ import annotations

from bs4 import BeautifulSoup
import socket
import time

from .exceptions import PTUConnectionError
from .models import PTUConfig, PTUNetworkStatus
from .web_client import PTUWebClient


def check_port_80(host: str, timeout_sec: float) -> tuple[bool, float | None, str | None]:
    started = time.perf_counter()
    try:
        with socket.create_connection((host, 80), timeout=timeout_sec):
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            return True, elapsed_ms, None
    except OSError as exc:
        return False, None, str(exc)


def collect_network_status(config: PTUConfig, client: PTUWebClient | None = None) -> PTUNetworkStatus:
    client = client or PTUWebClient(config)
    port_ok, port_latency_ms, port_error = check_port_80(config.host, config.timeout_sec)
    if not port_ok:
        summary = f"Port 80 is not reachable on {config.host}: {port_error}"
        return PTUNetworkStatus(
            host=config.host,
            base_url=config.base_url,
            port_80_reachable=False,
            http_ok=False,
            response_time_ms=port_latency_ms,
            summary=summary,
        )

    try:
        started = time.perf_counter()
        response = client.fetch_root_page()
        response_time_ms = (time.perf_counter() - started) * 1000.0
        title = _extract_title(response.text)
        summary = (
            f"PTU HTTP reachable at {config.base_url} "
            f"(status={response.status_code}, title={title or 'n/a'})"
        )
        return PTUNetworkStatus(
            host=config.host,
            base_url=config.base_url,
            port_80_reachable=True,
            http_ok=True,
            http_status_code=response.status_code,
            response_time_ms=response_time_ms,
            page_title=title,
            headers=dict(response.headers),
            summary=summary,
        )
    except PTUConnectionError as exc:
        return PTUNetworkStatus(
            host=config.host,
            base_url=config.base_url,
            port_80_reachable=True,
            http_ok=False,
            response_time_ms=port_latency_ms,
            summary=str(exc),
        )


def format_network_status(status: PTUNetworkStatus) -> str:
    return (
        f"PTU network status\n"
        f"- host: {status.host}\n"
        f"- base_url: {status.base_url}\n"
        f"- port_80_reachable: {status.port_80_reachable}\n"
        f"- http_ok: {status.http_ok}\n"
        f"- http_status_code: {status.http_status_code}\n"
        f"- response_time_ms: {status.response_time_ms}\n"
        f"- page_title: {status.page_title}\n"
        f"- summary: {status.summary}"
    )


def _extract_title(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    return soup.title.get_text(strip=True) if soup.title else None
