from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
import time
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BRIDGE_CONFIG_PATH = PROJECT_ROOT / "config" / "ptu_bridge.yaml"
DEFAULT_EXTERNAL_PTU_ROOT = Path("/home/ruirenmei/Downloads/flir_ptu_agent")


class PTUAdapterError(RuntimeError):
    """Raised when the bridge layer cannot safely talk to the standalone PTU project."""


class PTUAdapterImportError(PTUAdapterError):
    """Raised when the standalone ptu package cannot be imported."""


@dataclass(slots=True)
class PTUBridgeConfig:
    enabled: bool
    execute: bool
    host: str
    safe_pan_step: int
    safe_tilt_step: int
    safe_tilt_step_pos: int
    safe_tilt_step_neg: int
    max_pan_step: int
    max_tilt_step: int
    max_tilt_step_pos: int
    max_tilt_step_neg: int
    negative_tilt_cooldown_sec: float
    project_root: Path
    external_config_path: Path | None


def load_bridge_config(path: str | Path | None = None) -> PTUBridgeConfig:
    config_path = Path(path) if path is not None else DEFAULT_BRIDGE_CONFIG_PATH
    if not config_path.exists():
        raise PTUAdapterError(f"PTU bridge config not found: {config_path}")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    ptu_raw = raw.get("ptu")
    if not isinstance(ptu_raw, dict):
        raise PTUAdapterError("config/ptu_bridge.yaml must contain a top-level 'ptu' mapping.")

    project_root = Path(str(ptu_raw.get("project_root", DEFAULT_EXTERNAL_PTU_ROOT))).expanduser()
    external_config_value = str(ptu_raw.get("external_config_path", "")).strip()
    external_config_path = Path(external_config_value).expanduser() if external_config_value else None

    return PTUBridgeConfig(
        enabled=bool(ptu_raw.get("enabled", True)),
        execute=bool(ptu_raw.get("execute", False)),
        host=str(ptu_raw.get("host", "169.254.214.194")).strip(),
        safe_pan_step=int(ptu_raw.get("safe_pan_step", 10)),
        safe_tilt_step=int(ptu_raw.get("safe_tilt_step", 10)),
        safe_tilt_step_pos=int(ptu_raw.get("safe_tilt_step_pos", ptu_raw.get("safe_tilt_step", 10))),
        safe_tilt_step_neg=int(ptu_raw.get("safe_tilt_step_neg", ptu_raw.get("safe_tilt_step", 10))),
        max_pan_step=int(ptu_raw.get("max_pan_step", 20)),
        max_tilt_step=int(ptu_raw.get("max_tilt_step", 20)),
        max_tilt_step_pos=int(ptu_raw.get("max_tilt_step_pos", ptu_raw.get("max_tilt_step", 20))),
        max_tilt_step_neg=int(ptu_raw.get("max_tilt_step_neg", ptu_raw.get("max_tilt_step", 20))),
        negative_tilt_cooldown_sec=float(ptu_raw.get("negative_tilt_cooldown_sec", 0.5)),
        project_root=project_root,
        external_config_path=external_config_path,
    )


class PTUAdapter:
    def __init__(self, config_path: str | Path | None = None):
        self.bridge_config = load_bridge_config(config_path)
        self._controller = None
        self._ptu_error_types: tuple[type[BaseException], ...] = ()
        self._negative_tilt_cooldown_until = 0.0

    def connect(self) -> dict[str, Any]:
        controller = self._get_controller()
        try:
            network_status = controller.connect()
            return {
                "ready": bool(getattr(network_status, "http_ok", False)),
                "host": getattr(network_status, "host", self.bridge_config.host),
                "base_url": getattr(network_status, "base_url", ""),
                "port_80_reachable": bool(getattr(network_status, "port_80_reachable", False)),
                "http_ok": bool(getattr(network_status, "http_ok", False)),
                "summary": getattr(network_status, "summary", ""),
            }
        except self._ptu_error_types as exc:
            raise PTUAdapterError(f"PTU connect failed: {exc}") from exc

    def is_ready(self) -> bool:
        try:
            return bool(self.connect().get("ready"))
        except PTUAdapterError:
            return False

    def get_status(self) -> dict[str, Any]:
        controller = self._get_controller()
        try:
            status = controller.read_status()
            return {
                "PP": _maybe_int(status.get("PP")),
                "TP": _maybe_int(status.get("TP")),
                "PD": _maybe_int(status.get("PD")),
                "TD": _maybe_int(status.get("TD")),
                "C": status.get("C"),
                "status": status.get("status"),
            }
        except self._ptu_error_types as exc:
            raise PTUAdapterError(f"PTU status read failed: {exc}") from exc

    def get_pose(self) -> dict[str, Any]:
        status = self.get_status()
        return {
            "pan_position": status.get("PP"),
            "tilt_position": status.get("TP"),
            "pan_delta": status.get("PD"),
            "tilt_delta": status.get("TD"),
            "mode": status.get("C"),
        }

    def negative_tilt_cooldown_info(self) -> dict[str, Any]:
        remaining_sec = max(0.0, self._negative_tilt_cooldown_until - time.monotonic())
        return {
            "active": remaining_sec > 0.0,
            "remaining_sec": remaining_sec,
        }

    def pan_step(self, step: int, execute: bool = False) -> dict[str, Any]:
        return self._move(axis="pan", step=step, execute=execute)

    def tilt_step(self, step: int, execute: bool = False) -> dict[str, Any]:
        return self._move(axis="tilt", step=step, execute=execute)

    def halt(self, execute: bool = False) -> dict[str, Any]:
        controller = self._get_controller()
        actual_execute = bool(execute and self.bridge_config.execute and self.bridge_config.enabled)
        try:
            result = controller.halt(dry_run=not actual_execute)
            return {
                "axis": "halt",
                "executed": bool(getattr(result, "executed", False)),
                "dry_run": bool(getattr(result, "dry_run", True)),
                "response_status_code": getattr(result, "response_status_code", None),
                "details": getattr(result, "details", ""),
            }
        except self._ptu_error_types as exc:
            raise PTUAdapterError(f"PTU halt failed: {exc}") from exc

    def _move(self, axis: str, step: int, execute: bool) -> dict[str, Any]:
        controller = self._get_controller()
        clipped_step = self._clip_step(axis, step)
        actual_execute = bool(execute and self.bridge_config.execute and self.bridge_config.enabled)
        try:
            result, tilt_meta = self._run_move_command(
                controller=controller,
                axis=axis,
                step=clipped_step,
                actual_execute=actual_execute,
            )
            skipped_reason = tilt_meta["skipped_reason"]
            applied_step = clipped_step
            if axis == "tilt" and skipped_reason:
                applied_step = 0
            return {
                "axis": axis,
                "requested_step": step,
                "applied_step": applied_step,
                "executed": bool(getattr(result, "executed", False)),
                "dry_run": bool(getattr(result, "dry_run", True)),
                "response_status_code": getattr(result, "response_status_code", None),
                "details": getattr(result, "details", ""),
                "negative_tilt_cooldown_active": bool(tilt_meta["active"]),
                "negative_tilt_cooldown_remaining_sec": float(tilt_meta["remaining_sec"]),
                "negative_tilt_skipped_reason": skipped_reason,
            }
        except self._ptu_error_types as exc:
            raise PTUAdapterError(f"PTU {axis} move failed: {exc}") from exc

    def _run_move_command(self, controller, axis: str, step: int, actual_execute: bool):
        if axis == "pan":
            return controller.safe_pan_step(step=step, dry_run=not actual_execute), _default_tilt_meta()

        if not actual_execute:
            result = controller.safe_tilt_step(step=step, dry_run=True)
            return result, _default_tilt_meta()

        if step < 0:
            cooldown = self.negative_tilt_cooldown_info()
            if cooldown["active"]:
                return (
                    _build_skipped_tilt_result(
                        step=step,
                        reason="cooldown_active",
                        remaining_sec=float(cooldown["remaining_sec"]),
                    ),
                    {
                        "active": True,
                        "remaining_sec": float(cooldown["remaining_sec"]),
                        "skipped_reason": "cooldown_active",
                    },
                )

        try:
            result = controller.safe_tilt_step(step=step, dry_run=False)
            return result, _default_tilt_meta()
        except self._ptu_error_types as exc:
            if not actual_execute or step >= 0 or not _looks_like_http_500(exc):
                raise
            self._negative_tilt_cooldown_until = (
                time.monotonic() + self.bridge_config.negative_tilt_cooldown_sec
            )
            print(
                "[WARN] Negative tilt step failed with HTTP 500. "
                f"Skipping this tilt command and starting cooldown for "
                f"{self.bridge_config.negative_tilt_cooldown_sec:.2f}s."
            )
            return (
                _build_skipped_tilt_result(
                    step=step,
                    reason="http_500_cooldown",
                    remaining_sec=self.bridge_config.negative_tilt_cooldown_sec,
                ),
                {
                    "active": True,
                    "remaining_sec": self.bridge_config.negative_tilt_cooldown_sec,
                    "skipped_reason": "http_500_cooldown",
                },
            )

    def _clip_step(self, axis: str, step: int) -> int:
        if step == 0:
            return 0
        if axis == "pan":
            safe_limit = self.bridge_config.safe_pan_step
            max_limit = self.bridge_config.max_pan_step
        elif step > 0:
            safe_limit = self.bridge_config.safe_tilt_step_pos
            max_limit = self.bridge_config.max_tilt_step_pos
        else:
            safe_limit = self.bridge_config.safe_tilt_step_neg
            max_limit = self.bridge_config.max_tilt_step_neg
        limit = min(abs(max_limit), abs(safe_limit)) if safe_limit > 0 else abs(max_limit)
        if limit <= 0:
            raise PTUAdapterError(f"Invalid {axis} step limit in bridge config.")
        return max(-limit, min(limit, int(step)))

    def _get_controller(self):
        if self._controller is not None:
            return self._controller

        if not self.bridge_config.enabled:
            raise PTUAdapterError("PTU bridge is disabled in config/ptu_bridge.yaml.")

        try:
            controller_cls, load_config_func, error_types = _load_ptu_symbols(self.bridge_config)
            ptu_config = load_config_func(self.bridge_config.external_config_path)
            ptu_config.host = self.bridge_config.host
            ptu_config.max_pan_step = self.bridge_config.max_pan_step
            ptu_config.max_tilt_step = self.bridge_config.max_tilt_step
            self._controller = controller_cls(ptu_config)
            self._ptu_error_types = error_types
            return self._controller
        except PTUAdapterImportError:
            raise
        except Exception as exc:
            raise PTUAdapterError(f"Failed to initialize standalone PTU controller: {exc}") from exc


def _load_ptu_symbols(bridge_config: PTUBridgeConfig):
    project_root = bridge_config.project_root
    if not project_root.exists():
        raise PTUAdapterImportError(
            f"Standalone flir_ptu_agent not found at {project_root}. "
            "Either update config/ptu_bridge.yaml or install it with: pip install -e ../flir_ptu_agent"
        )
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    try:
        from ptu import PTUConnectionError, PTUControlNotImplementedError, PTUError, PTUResponseParseError
        from ptu import PTUController, load_config
    except Exception as exc:
        raise PTUAdapterImportError(
            "Unable to import the standalone 'ptu' package. "
            "You can fix this either by keeping project_root pointed at ../flir_ptu_agent "
            "or by running: pip install -e ../flir_ptu_agent"
        ) from exc

    return PTUController, load_config, (
        PTUError,
        PTUConnectionError,
        PTUControlNotImplementedError,
        PTUResponseParseError,
    )


def _maybe_int(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _looks_like_http_500(exc: BaseException) -> bool:
    message = str(exc)
    return "500" in message and "HTTP POST failed" in message


def _build_skipped_tilt_result(*, step: int, reason: str, remaining_sec: float):
    class _SkippedTiltResult:
        executed = False
        dry_run = False
        response_status_code = None

        def __init__(self):
            self.details = (
                f"Negative tilt command skipped. step={step} reason={reason} "
                f"cooldown_remaining_sec={remaining_sec:.2f}"
            )
            self.negative_tilt_cooldown_active = remaining_sec > 0.0
            self.negative_tilt_cooldown_remaining_sec = float(remaining_sec)
            self.negative_tilt_skipped_reason = reason

    return _SkippedTiltResult()


def _default_tilt_meta() -> dict[str, Any]:
    return {
        "active": False,
        "remaining_sec": 0.0,
        "skipped_reason": "",
    }
