from __future__ import annotations

from pathlib import Path
import ipaddress

import yaml

from .exceptions import PTUResponseParseError
from .models import PTUConfig


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "ptu.yaml"


def load_config(path: str | Path | None = None) -> PTUConfig:
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise PTUResponseParseError(f"Config file not found: {config_path}")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    ptu_raw = raw.get("ptu")
    if not isinstance(ptu_raw, dict):
        raise PTUResponseParseError("Config must contain a top-level 'ptu' mapping.")

    host = _require_non_empty_string(ptu_raw, "host")
    default_scheme = str(ptu_raw.get("default_scheme", "http")).strip().lower()
    if default_scheme not in {"http", "https"}:
        raise PTUResponseParseError("ptu.default_scheme must be 'http' or 'https'.")

    timeout_sec = float(ptu_raw.get("timeout_sec", 2.0))
    if timeout_sec <= 0:
        raise PTUResponseParseError("ptu.timeout_sec must be > 0.")

    max_pan_step = int(ptu_raw.get("max_pan_step", 50))
    max_tilt_step = int(ptu_raw.get("max_tilt_step", 50))
    if max_pan_step <= 0 or max_tilt_step <= 0:
        raise PTUResponseParseError("ptu.max_pan_step and ptu.max_tilt_step must be > 0.")

    artifacts_dir = str(ptu_raw.get("artifacts_dir", "artifacts")).strip() or "artifacts"
    planned_static_ip = _optional_ipv4_string(ptu_raw, "planned_static_ip")
    planned_subnet_mask = _optional_ipv4_string(ptu_raw, "planned_subnet_mask")
    planned_gateway = _optional_ipv4_string(ptu_raw, "planned_gateway")
    planned_host_pc_ip = _optional_ipv4_string(ptu_raw, "planned_host_pc_ip")

    return PTUConfig(
        host=host,
        timeout_sec=timeout_sec,
        verify_http=bool(ptu_raw.get("verify_http", False)),
        safe_mode=bool(ptu_raw.get("safe_mode", True)),
        max_pan_step=max_pan_step,
        max_tilt_step=max_tilt_step,
        default_scheme=default_scheme,
        artifacts_dir=artifacts_dir,
        planned_static_ip=planned_static_ip,
        planned_subnet_mask=planned_subnet_mask,
        planned_gateway=planned_gateway,
        planned_host_pc_ip=planned_host_pc_ip,
    )


def get_artifacts_dir(config: PTUConfig) -> Path:
    return PROJECT_ROOT / config.artifacts_dir


def _require_non_empty_string(raw: dict, key: str) -> str:
    value = str(raw.get(key, "")).strip()
    if not value:
        raise PTUResponseParseError(f"ptu.{key} must be a non-empty string.")
    return value


def _optional_ipv4_string(raw: dict, key: str) -> str | None:
    value = str(raw.get(key, "")).strip()
    if not value:
        return None
    try:
        ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise PTUResponseParseError(f"ptu.{key} must be a valid IPv4 address.") from exc
    return value
