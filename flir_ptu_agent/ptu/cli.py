from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import sys

from .config import load_config
from .controller import PTUController
from .diagnostics import format_network_status
from .discovery import summarize_discovery
from .exceptions import PTUControlNotImplementedError, PTUError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FLIR PTU web diagnostic and control CLI.")
    parser.add_argument(
        "--config",
        help="Path to YAML config file. Defaults to flir_ptu_agent/config/ptu.yaml",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="Check network reachability and root page status.")
    subparsers.add_parser("discover", help="Discover linked pages, forms, scripts, and likely endpoints.")
    subparsers.add_parser("status", help="Print config, device info, network status, and discovery summary.")

    move_pan = subparsers.add_parser("move-pan", help="Pan by a small safe step.")
    move_pan.add_argument("--step", type=int, required=True, help="Requested pan step.")
    move_pan.add_argument("--execute", action="store_true", help="Actually send the HTTP request.")

    move_tilt = subparsers.add_parser("move-tilt", help="Tilt by a small safe step.")
    move_tilt.add_argument("--step", type=int, required=True, help="Requested tilt step.")
    move_tilt.add_argument("--execute", action="store_true", help="Actually send the HTTP request.")

    halt = subparsers.add_parser("halt", help="Send a PTU halt command if a real endpoint is confirmed.")
    halt.add_argument("--execute", action="store_true", help="Actually send the HTTP request.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config(args.config)
    controller = PTUController(config)

    try:
        if args.command == "check":
            print(format_network_status(controller.get_network_status()))
            return 0

        if args.command == "discover":
            result = controller.discover_control_api()
            print(summarize_discovery(result))
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
            return 0

        if args.command == "status":
            print("[CONFIG]")
            print(json.dumps(asdict(config), ensure_ascii=False, indent=2))
            print("[NETWORK]")
            print(format_network_status(controller.get_network_status()))
            print("[DEVICE]")
            print(json.dumps(asdict(controller.get_device_info()), ensure_ascii=False, indent=2))
            print("[DISCOVERY]")
            print(summarize_discovery(controller.discover_control_api()))
            return 0

        if args.command == "move-pan":
            result = controller.safe_pan_step(step=args.step, dry_run=not args.execute)
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
            return 0

        if args.command == "move-tilt":
            result = controller.safe_tilt_step(step=args.step, dry_run=not args.execute)
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
            return 0

        if args.command == "halt":
            result = controller.halt(dry_run=not args.execute)
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
            return 0
    except PTUControlNotImplementedError as exc:
        print(f"[WARN] {exc}")
        return 2
    except PTUError as exc:
        print(f"[ERROR] {exc}")
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
