from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import time
from typing import Any

from .config import get_artifacts_dir
from .diagnostics import collect_network_status
from .discovery import discover_web_api
from .exceptions import (
    PTUConnectionError,
    PTUControlNotImplementedError,
    PTUDiscoveryError,
    PTUError,
    PTUResponseParseError,
)
from .models import PTUConfig, PTUDeviceInfo, PTUDiscoveryResult, PTUMoveResult, PTUNetworkStatus
from .web_client import PTUWebClient


DEVICE_PATTERNS = {
    "host_name": re.compile(r"Host Name[:\s]+([A-Za-z0-9._-]+)", re.IGNORECASE),
    "mac_address": re.compile(r"MAC[:\s]+([0-9A-Fa-f:]{17})", re.IGNORECASE),
    "firmware_version": re.compile(r"Firmware Version[:\s]+([A-Za-z0-9._-]+)", re.IGNORECASE),
}


class PTUController:
    def __init__(self, config: PTUConfig):
        self.config = config
        self.client = PTUWebClient(config)
        self._last_discovery: PTUDiscoveryResult | None = None

    def connect(self) -> PTUNetworkStatus:
        return self.get_network_status()

    def get_network_status(self) -> PTUNetworkStatus:
        return collect_network_status(self.config, self.client)

    def get_device_info(self) -> PTUDeviceInfo:
        response = self.client.fetch_root_page()
        info = PTUDeviceInfo(
            base_url=self.config.base_url,
            http_status_code=response.status_code,
            page_title=_extract_title(response.text),
            server_header=response.headers.get("Server"),
        )

        try:
            payload = self._query_ptcmd("V&NN&NM&NI&NS&NA&NG&VM")
            info.host_name = payload.get("NN")
            info.mac_address = payload.get("NA")
            info.firmware_version = payload.get("V")
            if payload.get("VM") and info.page_title:
                info.page_title = f"{info.page_title} ({payload['VM']})"
            return info
        except PTUError:
            for field_name, pattern in DEVICE_PATTERNS.items():
                match = pattern.search(response.text)
                if match:
                    setattr(info, field_name, match.group(1))
            return info

    def discover_control_api(self) -> PTUDiscoveryResult:
        try:
            self._last_discovery = discover_web_api(self.client, self.config)
            return self._last_discovery
        except PTUConnectionError as exc:
            raise PTUDiscoveryError(str(exc)) from exc

    def read_status(self) -> dict[str, Any]:
        return self._query_live_status()

    def read_network_info(self) -> dict[str, Any]:
        return self._query_ptcmd("V&NN&NM&NI&NS&NA&NG")

    def safe_pan_step(self, step: int, dry_run: bool = True) -> PTUMoveResult:
        return self._safe_move(axis="pan", step=step, dry_run=dry_run)

    def safe_tilt_step(self, step: int, dry_run: bool = True) -> PTUMoveResult:
        return self._safe_move(axis="tilt", step=step, dry_run=dry_run)

    def halt(self, dry_run: bool = True) -> PTUMoveResult:
        endpoint = self._get_control_endpoint("halt")
        command = endpoint.get("command", "H")
        if dry_run:
            return PTUMoveResult(
                axis="halt",
                step=None,
                dry_run=True,
                executed=False,
                endpoint=endpoint["url"],
                details=f"Dry run only. Would POST '{command}' to the confirmed halt endpoint.",
            )
        return self._execute_confirmed_endpoint(endpoint=endpoint, step=None, axis="halt")

    def _safe_move(self, axis: str, step: int, dry_run: bool) -> PTUMoveResult:
        self._validate_step(axis, step)
        endpoint = self._get_control_endpoint(axis)
        command = self._build_motion_command(axis=axis, step=step, endpoint=endpoint)
        if dry_run:
            return PTUMoveResult(
                axis=axis,
                step=step,
                dry_run=True,
                executed=False,
                endpoint=endpoint["url"],
                details=f"Dry run only. Would POST '{command}' to the confirmed {axis} endpoint.",
            )
        return self._execute_confirmed_endpoint(endpoint=endpoint, step=step, axis=axis)

    def _execute_confirmed_endpoint(
        self,
        endpoint: dict[str, Any],
        step: int | None,
        axis: str,
    ) -> PTUMoveResult:
        url = endpoint.get("url")
        method = str(endpoint.get("method", "GET")).upper()
        if not url:
            raise PTUControlNotImplementedError("Confirmed endpoint metadata is missing a URL.")

        command = endpoint.get("command")
        command_template = endpoint.get("command_template")
        if axis in {"pan", "tilt"}:
            command = self._build_motion_command(axis=axis, step=step, endpoint=endpoint)
        elif command is None and isinstance(command_template, str):
            command = command_template

        if not isinstance(command, str) or not command:
            raise PTUControlNotImplementedError(
                "A confirmed endpoint record exists, but it does not include an executable PT command."
            )

        if method != "POST":
            raise PTUControlNotImplementedError(
                f"Confirmed endpoint uses unsupported HTTP method for this agent: {method}"
            )

        before_status = self._query_live_status()
        response = self.client.post(url, data=command)
        response_text = response.text
        time.sleep(0.35)
        after_status = self._query_live_status()
        self._mark_endpoint_validated(axis, endpoint)
        artifact_path = self._save_execution_artifact(
            axis=axis,
            command=command,
            response_status_code=response.status_code,
            response_text=response_text,
            before_status=before_status,
            after_status=after_status,
        )
        return PTUMoveResult(
            axis=axis,
            step=step,
            dry_run=False,
            executed=True,
            endpoint=url,
            details=f"Executed confirmed {axis} endpoint with command='{command}'.",
            response_status_code=response.status_code,
            response_text=response_text,
            before_status=before_status,
            after_status=after_status,
            artifact_path=artifact_path,
        )

    def _get_control_endpoint(self, axis: str) -> dict[str, Any]:
        discovery = self._last_discovery or self.discover_control_api()
        for endpoint_group in (
            discovery.validated_control_endpoints,
            discovery.script_inferred_control_endpoints,
        ):
            for endpoint in endpoint_group:
                if endpoint.get("kind") == axis:
                    return endpoint

        raise PTUControlNotImplementedError(
            "PTU web control endpoint has not been safely confirmed yet. "
            "Discovery can inspect pages and scripts, but movement remains disabled "
            "until a real HTTP control endpoint is verified from the device UI or DevTools."
        )

    def _query_ptcmd(self, command: str) -> dict[str, Any]:
        endpoint = self._get_control_endpoint("status")
        response = self.client.post(endpoint["url"], data=command)
        try:
            payload = response.json()
        except ValueError as exc:
            raise PTUResponseParseError(
                f"PTCmd response was not valid JSON for command '{command}'."
            ) from exc
        if not isinstance(payload, dict):
            raise PTUResponseParseError("PTCmd JSON response must be an object.")
        return payload

    def _query_live_status(self) -> dict[str, Any]:
        return self._query_ptcmd("PP&TP&PD&TD&C")

    def _build_motion_command(self, axis: str, step: int | None, endpoint: dict[str, Any]) -> str:
        if step is None:
            raise PTUResponseParseError("Movement step cannot be None.")
        motion = self._query_ptcmd("PU&TU&PL&TL")
        pan_upper = _parse_positive_int(motion, "PU", default=6000)
        tilt_upper = _parse_positive_int(motion, "TU", default=6000)
        pan_lower = _parse_non_negative_int(motion, "PL")
        tilt_lower = _parse_non_negative_int(motion, "TL")

        pan_speed = _bounded_speed(abs(step), upper=pan_upper, lower=pan_lower)
        tilt_speed = _bounded_speed(abs(step), upper=tilt_upper, lower=tilt_lower)

        template = endpoint.get("command_template")
        if not isinstance(template, str):
            raise PTUControlNotImplementedError(
                "Confirmed movement endpoint is missing its command template."
            )
        return template.format(
            step=step,
            pan_speed=pan_speed,
            tilt_speed=tilt_speed,
        )

    def _mark_endpoint_validated(self, axis: str, endpoint: dict[str, Any]) -> None:
        discovery = self._last_discovery or self.discover_control_api()
        existing_kinds = {item.get("kind") for item in discovery.validated_control_endpoints}
        if axis not in existing_kinds:
            promoted = dict(endpoint)
            promoted["validated_at"] = datetime.now(timezone.utc).isoformat()
            discovery.validated_control_endpoints.append(promoted)
            artifacts_dir = get_artifacts_dir(self.config)
            path = artifacts_dir / "validated_control_endpoints.json"
            path.write_text(
                json.dumps(
                    discovery.validated_control_endpoints,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

    def _save_execution_artifact(
        self,
        axis: str,
        command: str,
        response_status_code: int,
        response_text: str,
        before_status: dict[str, Any],
        after_status: dict[str, Any],
    ) -> str:
        artifacts_dir = get_artifacts_dir(self.config) / "executions"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_path = artifacts_dir / f"{timestamp}_{axis}_execute.json"
        payload = {
            "axis": axis,
            "command": command,
            "response_status_code": response_status_code,
            "response_text": response_text,
            "before_status": before_status,
            "after_status": after_status,
        }
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(output_path)

    def _validate_step(self, axis: str, step: int) -> None:
        if not self.config.safe_mode:
            return

        limit = self.config.max_pan_step if axis == "pan" else self.config.max_tilt_step
        if abs(step) > limit:
            raise PTUResponseParseError(
                f"Requested {axis} step {step} exceeds safe limit {limit}."
            )


def _extract_title(html: str) -> str | None:
    match = re.search(r"<title>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return re.sub(r"\s+", " ", match.group(1)).strip()


def _parse_positive_int(payload: dict[str, Any], key: str, default: int) -> int:
    try:
        value = int(str(payload.get(key, default)))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def _parse_non_negative_int(payload: dict[str, Any], key: str) -> int:
    try:
        value = int(str(payload.get(key, 0)))
        return value if value >= 0 else 0
    except (TypeError, ValueError):
        return 0


def _bounded_speed(requested: int, upper: int, lower: int) -> int:
    if requested <= 0:
        requested = 1
    bounded = min(requested, max(upper, 1))
    return max(bounded, min(lower, max(upper, 1)))
