from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import argparse
import ipaddress
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ptu.config import get_artifacts_dir, load_config
from ptu.controller import PTUController
from ptu.exceptions import PTUError
from ptu.models import PTUStaticIPPlan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan a static-IP migration for the PTU without applying it."
    )
    parser.add_argument("--config", help="Optional config path.")
    parser.add_argument("--target-static-ip", help="Target PTU static IP.")
    parser.add_argument("--target-subnet-mask", help="Target PTU subnet mask.")
    parser.add_argument("--target-gateway", help="Target PTU gateway.")
    parser.add_argument("--planned-host-pc-ip", help="Recommended host-PC IP after migration.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_config(args.config)
    controller = PTUController(config)

    target_static_ip = _coalesce_ipv4(args.target_static_ip, config.planned_static_ip, "target-static-ip")
    target_subnet_mask = _coalesce_ipv4(args.target_subnet_mask, config.planned_subnet_mask, "target-subnet-mask")
    target_gateway = _coalesce_ipv4(args.target_gateway, config.planned_gateway, "target-gateway")
    planned_host_pc_ip = _coalesce_ipv4(
        args.planned_host_pc_ip,
        config.planned_host_pc_ip,
        "planned-host-pc-ip",
    )

    discovery = None
    current_status = {}
    current_mode = None
    try:
        discovery = controller.discover_control_api()
        current_status = asdict(controller.get_device_info())
        current_network = controller.read_network_info()
        current_mode = current_network.get("NM")
        current_status.update(current_network)
    except PTUError as exc:
        current_status = {"warning": str(exc)}

    network_endpoints = []
    if discovery is not None:
        network_endpoints = [
            endpoint
            for endpoint in discovery.script_inferred_control_endpoints
            if str(endpoint.get("kind", "")).startswith("network_")
        ]
    elif (get_artifacts_dir(config) / "discovery.json").exists():
        cached_discovery = json.loads((get_artifacts_dir(config) / "discovery.json").read_text(encoding="utf-8"))
        network_endpoints = [
            endpoint
            for endpoint in cached_discovery.get("script_inferred_control_endpoints", [])
            if isinstance(endpoint, dict) and str(endpoint.get("kind", "")).startswith("network_")
        ]

    plan = PTUStaticIPPlan(
        current_host=config.host,
        current_base_url=config.base_url,
        current_mode=current_mode,
        current_ip=_clean_or_none(current_status.get("NI")),
        current_subnet_mask=_clean_or_none(current_status.get("NS")),
        current_gateway=_clean_or_none(current_status.get("NG")),
        target_static_ip=target_static_ip,
        target_subnet_mask=target_subnet_mask,
        target_gateway=target_gateway,
        recommended_host_pc_ip=planned_host_pc_ip,
        script_inferred_network_endpoints=network_endpoints,
        warnings=[
            "This script only generates a migration plan. It does not write PTU network settings.",
            "Changing PTU network settings can drop the current HTTP session immediately.",
            "The web UI suggests Set/Save/Reset network actions exist, but this project does not execute them by default.",
        ],
        config_update={
            "host": target_static_ip,
            "planned_static_ip": target_static_ip,
            "planned_subnet_mask": target_subnet_mask,
            "planned_gateway": target_gateway,
            "planned_host_pc_ip": planned_host_pc_ip,
        },
    )

    artifact_path = save_plan_artifact(config, plan)
    print(render_plan(plan, artifact_path))
    print(json.dumps(asdict(plan), ensure_ascii=False, indent=2))
    return 0


def render_plan(plan: PTUStaticIPPlan, artifact_path: Path) -> str:
    return (
        "[PLAN] Static IP migration\n"
        f"- current_host: {plan.current_host}\n"
        f"- current_base_url: {plan.current_base_url}\n"
        f"- current_mode: {plan.current_mode or 'unknown'}\n"
        f"- current_ip: {plan.current_ip or 'unknown'}\n"
        f"- current_subnet_mask: {plan.current_subnet_mask or 'unknown'}\n"
        f"- current_gateway: {plan.current_gateway or 'unknown'}\n"
        f"- target_ptu_ip: {plan.target_static_ip or 'unset'}\n"
        f"- target_subnet_mask: {plan.target_subnet_mask or 'unset'}\n"
        f"- target_gateway: {plan.target_gateway or 'unset'}\n"
        f"- recommended_host_pc_ip: {plan.recommended_host_pc_ip or 'unset'}\n"
        f"- plan_artifact: {artifact_path}"
    )


def save_plan_artifact(config, plan: PTUStaticIPPlan) -> Path:
    output_dir = get_artifacts_dir(config) / "network_changes"
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    plan_path = output_dir / f"{timestamp}_static_ip_plan.json"
    latest_path = output_dir / "latest_static_ip_plan.json"
    payload = asdict(plan)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    plan_path.write_text(text, encoding="utf-8")
    latest_path.write_text(text, encoding="utf-8")
    return plan_path


def _coalesce_ipv4(cli_value: str | None, config_value: str | None, label: str) -> str | None:
    value = cli_value or config_value
    if value is None:
        return None
    try:
        ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise SystemExit(f"[ERROR] {label} must be a valid IPv4 address.") from exc
    return value


def _clean_or_none(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


if __name__ == "__main__":
    raise SystemExit(main())
