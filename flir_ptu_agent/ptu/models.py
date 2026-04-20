from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class PTUConfig:
    host: str
    timeout_sec: float = 2.0
    verify_http: bool = False
    safe_mode: bool = True
    max_pan_step: int = 50
    max_tilt_step: int = 50
    default_scheme: str = "http"
    artifacts_dir: str = "artifacts"
    planned_static_ip: str | None = None
    planned_subnet_mask: str | None = None
    planned_gateway: str | None = None
    planned_host_pc_ip: str | None = None

    @property
    def base_url(self) -> str:
        return f"{self.default_scheme}://{self.host}"


@dataclass(slots=True)
class PTUDeviceInfo:
    base_url: str
    http_status_code: int | None = None
    page_title: str | None = None
    server_header: str | None = None
    host_name: str | None = None
    mac_address: str | None = None
    firmware_version: str | None = None


@dataclass(slots=True)
class PTUNetworkStatus:
    host: str
    base_url: str
    port_80_reachable: bool
    http_ok: bool
    http_status_code: int | None = None
    response_time_ms: float | None = None
    page_title: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    summary: str = ""


@dataclass(slots=True)
class PTUDiscoveryResult:
    base_url: str
    fetched_at: str
    page_title: str | None
    root_status_code: int
    fetched_page_urls: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    forms: list[dict[str, Any]] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    fetched_script_urls: list[str] = field(default_factory=list)
    keyword_hits: dict[str, list[str]] = field(default_factory=dict)
    likely_control_endpoints: list[dict[str, Any]] = field(default_factory=list)
    script_inferred_control_endpoints: list[dict[str, Any]] = field(default_factory=list)
    validated_control_endpoints: list[dict[str, Any]] = field(default_factory=list)
    artifacts_dir: str = ""
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PTUMoveResult:
    axis: str
    step: int | None
    dry_run: bool
    executed: bool
    endpoint: str | None = None
    details: str = ""
    response_status_code: int | None = None
    response_text: str | None = None
    before_status: dict[str, Any] = field(default_factory=dict)
    after_status: dict[str, Any] = field(default_factory=dict)
    artifact_path: str | None = None


@dataclass(slots=True)
class PTUStaticIPPlan:
    current_host: str
    current_base_url: str
    current_mode: str | None = None
    current_ip: str | None = None
    current_subnet_mask: str | None = None
    current_gateway: str | None = None
    target_static_ip: str | None = None
    target_subnet_mask: str | None = None
    target_gateway: str | None = None
    recommended_host_pc_ip: str | None = None
    script_inferred_network_endpoints: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    config_update: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PTUCalibrationStepResult:
    axis: str
    requested_step: int
    success: bool
    dry_run: bool
    before_status: dict[str, Any] = field(default_factory=dict)
    after_status: dict[str, Any] = field(default_factory=dict)
    delta_PP: int | None = None
    delta_TP: int | None = None
    response_text: str | None = None
    halt_response_text: str | None = None
    error: str | None = None


@dataclass(slots=True)
class PTUCalibrationSummary:
    axis: str
    dry_run: bool
    requested_steps: list[int] = field(default_factory=list)
    results: list[PTUCalibrationStepResult] = field(default_factory=list)
    stopped_on_error: bool = False
    notes: list[str] = field(default_factory=list)
